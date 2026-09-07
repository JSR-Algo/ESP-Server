#!/usr/bin/env python3
"""Signed, non-opening admission gate for Course Mode physical firmware flashing."""
from __future__ import annotations

import argparse, glob, hashlib, json, os, re, stat, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import course_mode_candidate_manifest as candidate_manifest
from course_mode_physical_tft_preflight import PINNED_APPROVAL_KEY_FINGERPRINT, PINNED_APPROVAL_PUBLIC_KEY_RAW

VALIDATOR = "course-mode-physical-flash-admission.v1"
COURSE_ID, COURSE_KEY = "a17792f6-8d86-4ad1-a6f3-77663b4d4674", "english-6month-4-6"
ROBOT_MAC, BOARD, TARGET, SERIAL_PATH = "14:c1:9f:d1:ac:20", "LCDWiki ES3C35P", "esp32s3", "/dev/cu.usbmodem1101"
FIRMWARE_SHA = "b54c6ca33e9beb3747b44feceb7c64fea33fe1d6"
APP_SHA256, APP_BYTES, APP_OFFSET, PARTITION_BYTES = "782020e2f8ac44bd197f57e9e126c196286a8223005de1e425f8290f12b28dff", 3637200, "0x20000", 4128768
MANIFEST_SHA256 = "23b70849b6b65901b01b38e91279455a2e5e8d13989438e2e0bbfa707602aa65"
MAX_JSON_BYTES, MAX_LSOF_OUTPUT_BYTES, LSOF_TIMEOUT_SECONDS, CHECK_FRESHNESS_SECONDS = 1024 * 1024, 64 * 1024, 3.0, 300
TRUSTED_LSOF_EXECUTABLE = next((p for p in (Path("/usr/sbin/lsof"), Path("/usr/bin/lsof")) if p.is_file()), Path("/usr/sbin/lsof"))
TOP_KEYS = {"schemaVersion", "sessionId", "checkedAt", "candidate", "robot", "serialLease", "flashPlan", "safety"}
CANDIDATE_KEYS = {"candidateId", "courseId", "courseKey", "createdAt", "expiresAt", "path", "sha256", "repositories", "images", "firmware"}
IDENTITY_KEYS = {"schemaVersion", "sessionId", "candidate", "partitionTable", "robot", "signer"}
REPOSITORY_KEYS = {"path", "sha", "branch", "remoteUrl", "dirtyExceptions"}
IMAGE_KEYS = {"reference", "id", "platform", "provenanceLabels"}
ROBOT_KEYS = {"mac", "board", "target", "serialPath", "exactlyOneRobot"}
SERIAL_KEYS = {"soleLeaseConfirmed", "competingProcessesStopped", "devicePath", "discoveredDevices", "holderPids", "inventoryMethod"}
FLASH_KEYS = {"operation", "protectedPartitions", "preserveProtectedPartitions"}
OPERATION_KEYS = {"operation", "offset", "imageSha256", "imageBytes", "after", "eraseChip", "mergedImage"}
SAFETY_KEYS = {"adultObserverPresent", "motionAreaClearAndSecured", "immediatePowerIsolationReachable", "stablePower", "stableLan", "evidenceCaptureReady", "soleUsbSerialLease", "preserveBootloader", "preservePartitionTable", "preserveNvs", "preserveOtaData", "preservePhyInit", "preserveReserved", "preserveGeneratedAssets"}
EXPECTED_PARTITIONS = [
 {"name":"bootloader","offset":"0x0","size":"0x8000","end":"0x8000","protected":True},
 {"name":"partition-table","offset":"0x8000","size":"0x1000","end":"0x9000","protected":True},
 {"name":"nvs","offset":"0x9000","size":"0x4000","end":"0xd000","protected":True},
 {"name":"ota-data","offset":"0xd000","size":"0x2000","end":"0xf000","protected":True},
 {"name":"phy-init","offset":"0xf000","size":"0x1000","end":"0x10000","protected":True},
 {"name":"reserved","offset":"0x10000","size":"0x10000","end":"0x20000","protected":True},
 {"name":"application","offset":"0x20000","size":"0x7e0000","end":"0x800000","protected":False},
 {"name":"generated-assets","offset":"0x800000","size":"0x800000","end":"0x1000000","protected":True},
]

class DuplicateKeyError(ValueError): pass
def utc_now(): return datetime.now(timezone.utc)
def _canonical_bytes(value): return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
def _strict_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise DuplicateKeyError(key)
        result[key] = value
    return result
def _load_json(raw):
    try: return json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_pairs, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x))), None
    except DuplicateKeyError: return None, "duplicate_key"
    except (UnicodeError, ValueError, json.JSONDecodeError): return None, "invalid_json"
def _path_has_symlink(path):
    current = path
    while current != current.parent:
        if current.is_symlink(): return True
        current = current.parent
    return False
def _secure_read(path, limit):
    if not path.is_absolute() or _path_has_symlink(path): return None, "path"
    try: fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError: return None, "open"
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid != os.getuid() or before.st_mode & 0o222 or before.st_size > limit: return None, "metadata"
        data = bytearray()
        while True:
            chunk = os.read(fd, 65536)
            if not chunk: break
            data.extend(chunk)
            if len(data) > limit: return None, "size"
        after = os.fstat(fd)
        if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns): return None, "changed"
        return bytes(data), None
    except OSError: return None, "read"
    finally: os.close(fd)
def _parse_utc(value):
    if not isinstance(value,str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z",value) is None: return None
    try: parsed=datetime.fromisoformat(value[:-1]+"+00:00")
    except ValueError: return None
    canonical=parsed.isoformat(timespec="microseconds").replace("+00:00","Z").replace(".000000Z","Z")
    return parsed if canonical==value else None
def _verify_signature(data, signature):
    fingerprint=hashlib.sha256(PINNED_APPROVAL_PUBLIC_KEY_RAW).hexdigest() if PINNED_APPROVAL_PUBLIC_KEY_RAW else ""
    if fingerprint != PINNED_APPROVAL_KEY_FINGERPRINT: return False, fingerprint
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(PINNED_APPROVAL_PUBLIC_KEY_RAW).verify(signature,data)
        return True, fingerprint
    except Exception: return False, fingerprint

def collect_serial_inventory():
    devices=[]
    for name in sorted(glob.glob("/dev/cu.usb*")):
        try: metadata=os.lstat(name); resolved=os.stat(name)
        except OSError: continue
        if stat.S_ISCHR(metadata.st_mode) and stat.S_ISCHR(resolved.st_mode): devices.append(name)
    result=candidate_manifest.run_bounded_command([str(TRUSTED_LSOF_EXECUTABLE),"-nP","-t","--",SERIAL_PATH],cwd=Path("/"),timeout_sec=LSOF_TIMEOUT_SECONDS,max_output_bytes=MAX_LSOF_OUTPUT_BYTES,env={"PATH":"/usr/bin:/bin","LANG":"C","LC_ALL":"C","HOME":"/nonexistent"})
    if result.error: return devices,[],result.error
    if result.returncode not in (0,1): return devices,[],"exit"
    holders=[]
    for line in result.stdout.splitlines():
        token=line.strip().removeprefix("p")
        if token.isdigit(): holders.append(int(token))
        elif token: return devices,[],"output"
    return devices,sorted(set(holders)),None

def _candidate_matches(binding, actual):
    if not isinstance(actual,dict): return False
    if any(actual.get(k)!=binding.get(k) for k in ("candidateId","createdAt","expiresAt")): return False
    if actual.get("course")!={"courseId":binding.get("courseId"),"courseKey":binding.get("courseKey")}: return False
    repos=binding.get("repositories",{})
    if actual.get("repositories",{}).get("adminEsp")!=repos.get("admin") or actual.get("repositories",{}).get("backend")!=repos.get("backend") or actual.get("repositories",{}).get("firmware")!=repos.get("firmware"): return False
    images=binding.get("images",{}); actual_images=actual.get("images",{})
    for signed_name,actual_name in (("backend","lessonStudioBackend"),("web","lessonStudioWeb")):
        if not isinstance(actual_images.get(actual_name),dict) or any(actual_images[actual_name].get(k)!=images.get(signed_name,{}).get(k) for k in ("reference","id")): return False
    firmware=binding.get("firmware",{}); app=firmware.get("app",{}); manifest=firmware.get("manifest",{}); actual_firmware=actual.get("firmware",{})
    expected_fields={"appPath":app.get("path"),"appOffset":app.get("offset"),"appBytes":app.get("bytes"),"appSha256":app.get("sha256"),"partitionBytes":app.get("partitionBytes"),"evidenceManifestPath":manifest.get("path"),"evidenceManifestSha256":manifest.get("sha256")}
    return isinstance(actual_firmware,dict) and all(actual_firmware.get(k)==v for k,v in expected_fields.items())
def _validate_candidate_shape(value,reasons):
    if not isinstance(value,dict) or set(value)!=CANDIDATE_KEYS: reasons.add("candidate.schema"); return
    if value.get("courseId")!=COURSE_ID or value.get("courseKey")!=COURSE_KEY: reasons.add("candidate.course")
    repos=value.get("repositories")
    if not isinstance(repos,dict) or set(repos)!={"admin","backend","firmware"}: reasons.add("candidate.repositories"); repos={}
    for name,repo in repos.items():
        if not isinstance(repo,dict) or set(repo)!=REPOSITORY_KEYS or repo.get("dirtyExceptions")!=[]: reasons.add(f"candidate.repositories.{name}")
    if isinstance(repos.get("firmware"),dict) and repos["firmware"].get("sha")!=FIRMWARE_SHA: reasons.add("candidate.repositories.firmware")
    images=value.get("images")
    if not isinstance(images,dict) or set(images)!={"backend","web"}: reasons.add("candidate.images"); images={}
    for name,image in images.items():
        repo=repos.get("backend" if name=="backend" else "admin",{})
        labels={"org.opencontainers.image.revision":repo.get("sha"),"org.opencontainers.image.source":repo.get("remoteUrl")}
        if not isinstance(image,dict) or set(image)!=IMAGE_KEYS or image.get("platform")!="linux/arm64" or image.get("provenanceLabels")!=labels: reasons.add(f"candidate.images.{name}")
    fw=value.get("firmware"); expected_app={"sha256":APP_SHA256,"bytes":APP_BYTES,"offset":APP_OFFSET,"partitionBytes":PARTITION_BYTES}
    if not isinstance(fw,dict) or set(fw)!={"board","target","gitSha","app","manifest"}: reasons.add("candidate.firmware"); return
    if (fw.get("board"),fw.get("target"),fw.get("gitSha"))!=(BOARD,TARGET,FIRMWARE_SHA): reasons.add("candidate.firmware")
    app=fw.get("app"); manifest=fw.get("manifest")
    if not isinstance(app,dict) or set(app)!={"path",*expected_app} or not Path(str(app.get("path",""))).is_absolute() or any(app.get(k)!=v for k,v in expected_app.items()): reasons.add("candidate.firmware.app")
    if not isinstance(manifest,dict) or set(manifest)!={"path","sha256"} or not Path(str(manifest.get("path",""))).is_absolute() or manifest.get("sha256")!=MANIFEST_SHA256: reasons.add("candidate.firmware.manifest")

def validate_documents(doc,identity,actual,now,devices,holders,inventory_error):
    reasons=set()
    if not isinstance(doc,dict) or set(doc)!=TOP_KEYS or type(doc.get("schemaVersion")) is not int or doc.get("schemaVersion")!=1: return ["input.schema"]
    if not isinstance(identity,dict) or set(identity)!=IDENTITY_KEYS or type(identity.get("schemaVersion")) is not int or identity.get("schemaVersion")!=1: return ["expectedIdentity.schema"]
    try: valid_uuid=str(uuid.UUID(doc.get("sessionId","")))==doc.get("sessionId")
    except (ValueError,TypeError,AttributeError): valid_uuid=False
    if not valid_uuid or doc.get("sessionId")!=identity.get("sessionId"): reasons.add("sessionId")
    checked=_parse_utc(doc.get("checkedAt"))
    if checked is None: reasons.add("checkedAt")
    elif abs((now-checked).total_seconds())>CHECK_FRESHNESS_SECONDS: reasons.add("checkedAt.stale")
    binding=doc.get("candidate"); _validate_candidate_shape(binding,reasons)
    if binding!=identity.get("candidate"): reasons.add("candidate.identity")
    created=_parse_utc(binding.get("createdAt")) if isinstance(binding,dict) else None; expires=_parse_utc(binding.get("expiresAt")) if isinstance(binding,dict) else None
    if created is None or expires is None or not(created<=now<expires): reasons.add("candidate.time")
    if isinstance(binding,dict) and not _candidate_matches(binding,actual): reasons.add("candidate.reference")
    if identity.get("partitionTable")!=EXPECTED_PARTITIONS: reasons.add("expectedIdentity.partitionTable")
    if identity.get("signer")!={"algorithm":"ed25519","fingerprint":PINNED_APPROVAL_KEY_FINGERPRINT}: reasons.add("expectedIdentity.signer")
    expected_robot={"mac":ROBOT_MAC,"board":BOARD,"target":TARGET,"serialPath":SERIAL_PATH,"exactlyOneRobot":True}
    if doc.get("robot")!=identity.get("robot") or doc.get("robot")!=expected_robot: reasons.add("robot.identity")
    expected_lease={"soleLeaseConfirmed":True,"competingProcessesStopped":True,"devicePath":SERIAL_PATH,"discoveredDevices":[SERIAL_PATH],"holderPids":[],"inventoryMethod":"lstat-glob-lsof-v1"}
    lease=doc.get("serialLease")
    if not isinstance(lease,dict) or set(lease)!=SERIAL_KEYS or lease!=expected_lease: reasons.add("serialLease")
    if inventory_error: reasons.add(f"serial.lsof.{inventory_error}")
    if devices!=[SERIAL_PATH]: reasons.add("serial.inventory")
    if holders: reasons.add("serial.occupied")
    plan=doc.get("flashPlan"); operation={"operation":"write_flash","offset":APP_OFFSET,"imageSha256":APP_SHA256,"imageBytes":APP_BYTES,"after":"no-reset","eraseChip":False,"mergedImage":False}; protected=[p for p in EXPECTED_PARTITIONS if p["protected"]]
    if not isinstance(plan,dict) or set(plan)!=FLASH_KEYS: reasons.add("flashPlan.keys")
    else:
        if not isinstance(plan.get("operation"),dict) or set(plan["operation"])!=OPERATION_KEYS or plan["operation"]!=operation: reasons.add("flashPlan.operation")
        if plan.get("protectedPartitions")!=protected: reasons.add("flashPlan.protectedPartitions")
        if plan.get("preserveProtectedPartitions") is not True: reasons.add("flashPlan.preserveProtectedPartitions")
    safety=doc.get("safety")
    if not isinstance(safety,dict) or set(safety)!=SAFETY_KEYS: reasons.add("safety.keys")
    else:
        for key in sorted(SAFETY_KEYS):
            if safety.get(key) is not True: reasons.add(f"safety.{key}")
    return sorted(reasons)

def _failure(reasons): print(json.dumps({"schemaVersion":1,"validator":VALIDATOR,"status":"fail","reasons":sorted(set(reasons)),"physicalActionsPerformed":False,"serialOpened":False},sort_keys=True,separators=(",",":")))
def _publish(path,payload):
    if not path.is_absolute() or path.exists() or path.is_symlink() or _path_has_symlink(path.parent): return False
    data=(json.dumps(payload,sort_keys=True,separators=(",",":"),allow_nan=False)+"\n").encode()
    try: fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0),0o600)
    except OSError: return False
    try:
        view=memoryview(data)
        while view: view=view[os.write(fd,view):]
        os.fsync(fd)
    finally: os.close(fd)
    return True
def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for flag in ("input","output","expected-identity","expected-identity-signature"): parser.add_argument("--"+flag,required=True,type=Path)
    args=parser.parse_args(argv)
    if args.output.exists() or args.output.is_symlink() or not args.output.is_absolute(): _failure(["output.path"]); return 1
    raw,error=_secure_read(args.input,MAX_JSON_BYTES)
    if error or raw is None: _failure(["input.unreadable"]); return 1
    doc,error=_load_json(raw)
    if error: _failure([f"input.{error}"]); return 1
    if not isinstance(doc,dict) or set(doc)!=TOP_KEYS or type(doc.get("schemaVersion")) is not int or doc.get("schemaVersion")!=1:
        _failure(["input.schema"]); return 1
    identity_raw,error=_secure_read(args.expected_identity,MAX_JSON_BYTES)
    if error or identity_raw is None: _failure(["expectedIdentity.unreadable"]); return 1
    identity,error=_load_json(identity_raw)
    if error: _failure([f"expectedIdentity.{error}"]); return 1
    signature,error=_secure_read(args.expected_identity_signature,256)
    if error or signature is None or len(signature)!=64: _failure(["expectedIdentity.signature"]); return 1
    valid,fingerprint=_verify_signature(_canonical_bytes(identity),signature)
    if not valid: _failure(["expectedIdentity.signature"]); return 1
    binding=doc.get("candidate") if isinstance(doc,dict) else None
    candidate_raw,error=_secure_read(Path(binding.get("path","")),MAX_JSON_BYTES) if isinstance(binding,dict) else (None,"path")
    if error or candidate_raw is None or hashlib.sha256(candidate_raw).hexdigest()!=binding.get("sha256"): _failure(["candidate.input"]); return 1
    actual,error=_load_json(candidate_raw)
    if error: _failure(["candidate.input"]); return 1
    now=utc_now(); reasons=[f"candidate.{r}" for r in candidate_manifest.validate_candidate(actual,now=now)]
    devices,holders,inventory_error=collect_serial_inventory(); reasons.extend(validate_documents(doc,identity,actual,now,devices,holders,inventory_error))
    if reasons: _failure(reasons); return 1
    payload={"schemaVersion":1,"validator":VALIDATOR,"status":"pass","reasons":[],"candidateId":binding["candidateId"],"sessionId":doc["sessionId"],"signerFingerprint":fingerprint,"physicalActionsPerformed":False,"serialOpened":False,"inputSha256":hashlib.sha256(raw).hexdigest(),"expectedIdentitySha256":hashlib.sha256(identity_raw).hexdigest(),"candidateSha256":hashlib.sha256(candidate_raw).hexdigest(),"robotMac":ROBOT_MAC,"serialPath":SERIAL_PATH,"firmwareSha":FIRMWARE_SHA,"appSha256":APP_SHA256,"manifestSha256":MANIFEST_SHA256}
    if not _publish(args.output,payload): _failure(["output.path"]); return 1
    return 0
if __name__=="__main__": raise SystemExit(main())
