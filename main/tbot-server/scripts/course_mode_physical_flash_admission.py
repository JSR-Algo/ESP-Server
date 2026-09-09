#!/usr/bin/env python3
"""Signed, non-opening admission gate for Course Mode physical firmware flashing."""
from __future__ import annotations

import argparse, contextlib, copy, glob, hashlib, json, math, os, re, selectors, signal, stat, subprocess, time, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import course_mode_candidate_manifest as candidate_manifest
import course_mode_software_evidence_snapshot as software_snapshot
from course_mode_physical_tft_preflight import PINNED_APPROVAL_KEY_FINGERPRINT, PINNED_APPROVAL_PUBLIC_KEY_RAW

VALIDATOR = "course-mode-physical-flash-admission.v1"
COURSE_ID, COURSE_KEY = "a17792f6-8d86-4ad1-a6f3-77663b4d4674", "english-6month-4-6"
ROBOT_MAC, BOARD, TARGET, SERIAL_PATH = "14:c1:9f:d1:ac:20", "LCDWiki ES3C35P", "esp32s3", "/dev/cu.usbmodem1101"
FIRMWARE_SHA = "b54c6ca33e9beb3747b44feceb7c64fea33fe1d6"
APP_SHA256, APP_BYTES, APP_OFFSET, PARTITION_BYTES = "782020e2f8ac44bd197f57e9e126c196286a8223005de1e425f8290f12b28dff", 3637200, "0x20000", 4128768
MANIFEST_SHA256 = "23b70849b6b65901b01b38e91279455a2e5e8d13989438e2e0bbfa707602aa65"
MAX_JSON_BYTES, MAX_LSOF_OUTPUT_BYTES, LSOF_TIMEOUT_SECONDS, CHECK_FRESHNESS_SECONDS = 1024 * 1024, 64 * 1024, 3.0, 300
OPERATOR_UID = 501
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
def _exact_equal(left,right):
    if type(left) is not type(right): return False
    if isinstance(left,dict): return set(left)==set(right) and all(_exact_equal(left[key],right[key]) for key in left)
    if isinstance(left,list): return len(left)==len(right) and all(_exact_equal(a,b) for a,b in zip(left,right))
    return left==right
def _strict_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise DuplicateKeyError(key)
        result[key] = value
    return result
def _load_json(raw):
    def finite(value):
        parsed=float(value)
        if not math.isfinite(parsed): raise ValueError(value)
        return parsed
    try: return json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_pairs, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)), parse_float=finite), None
    except DuplicateKeyError: return None, "duplicate_key"
    except (UnicodeError, ValueError, json.JSONDecodeError): return None, "invalid_json"
def _file_identity(value):
    return (value.st_dev,value.st_ino,value.st_mode,value.st_nlink,value.st_uid,value.st_gid,value.st_size,value.st_mtime_ns,value.st_ctime_ns)
def _authority_identity(value): return (value.st_dev,value.st_ino,value.st_mode,value.st_uid,value.st_gid)
def _secure_parent(path):
    if os.geteuid()!=OPERATOR_UID: raise OSError("operator uid")
    return candidate_manifest._open_trusted_source_directory(path)
def _parent_still_bound(path,fd,metadata,ancestry):
    return candidate_manifest._trusted_source_directory_still_named(path,fd,metadata,ancestry)
def _output_parent_still_bound(path,fd,metadata,saved_ancestry):
    other=None
    try:
        other,observed,new_ancestry=_secure_parent(path)
        fields=lambda value:(value.st_dev,value.st_ino,value.st_mode,value.st_uid,value.st_gid)
        return fields(os.fstat(fd))==fields(metadata)==fields(observed) and new_ancestry==saved_ancestry
    except OSError: return False
    finally:
        if other is not None: os.close(other)
def _secure_read(path, limit):
    if not path.is_absolute() or ".." in path.parts: return None, "path"
    parent_fd=file_fd=None
    try:
        parent_fd,parent_metadata,ancestry=_secure_parent(path.parent)
        named_before=os.stat(path.name,dir_fd=parent_fd,follow_symlinks=False)
        file_fd=os.open(path.name,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0),dir_fd=parent_fd)
        before = os.fstat(file_fd)
        if _file_identity(named_before)!=_file_identity(before) or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid != OPERATOR_UID or before.st_mode & 0o222 or before.st_size > limit: return None, "metadata"
        data = bytearray()
        while True:
            chunk = os.read(file_fd, min(65536,limit+1-len(data)))
            if not chunk: break
            data.extend(chunk)
            if len(data) > limit: return None, "size"
        after=os.fstat(file_fd); named_after=os.stat(path.name,dir_fd=parent_fd,follow_symlinks=False)
        if _file_identity(before)!=_file_identity(after) or _file_identity(before)!=_file_identity(named_after) or len(data)!=before.st_size or not _parent_still_bound(path.parent,parent_fd,parent_metadata,ancestry): return None,"changed"
        return (bytes(data),_file_identity(before),path,parent_metadata,ancestry), None
    except OSError: return None, "read"
    finally:
        if file_fd is not None: os.close(file_fd)
        if parent_fd is not None: os.close(parent_fd)
def _still_bound(descriptor):
    _,identity,path,saved_metadata,saved_ancestry=descriptor
    parent_fd=verification_fd=None
    try:
        parent_fd,_,_=_secure_parent(path.parent)
        current=os.stat(path.name,dir_fd=parent_fd,follow_symlinks=False)
        verification_fd,observed,new_ancestry=_secure_parent(path.parent)
        authority=lambda value:(value.st_dev,value.st_ino,value.st_mode,value.st_uid,value.st_gid)
        return (_file_identity(current)==identity and authority(os.fstat(parent_fd))==authority(saved_metadata)==authority(observed) and new_ancestry==saved_ancestry)
    except OSError: return False
    finally:
        if verification_fd is not None: os.close(verification_fd)
        if parent_fd is not None: os.close(parent_fd)
def _external_regular_binding(path,limit):
    if not path.is_absolute() or ".." in path.parts: return None
    try:
        descriptor,error=candidate_manifest.secure_regular_descriptor(path,limit,secure_metadata=True,bind_parent=True)
        if error or descriptor is None: return None
        identity=_file_identity(os.stat(path,follow_symlinks=False))
        parent=_external_directory_binding(path.parent)
        repeated,repeated_error=candidate_manifest.secure_regular_descriptor(path,limit,secure_metadata=True,bind_parent=True)
        if repeated_error or not _exact_equal(repeated,descriptor) or _file_identity(os.stat(path,follow_symlinks=False))!=identity or parent is None: return None
        _,parent_identity,ancestry=parent
        return (descriptor["sha256"],identity,path,parent_identity,ancestry)
    except OSError: return None
def _external_regular_still_bound(record):
    digest,identity,path,parent_metadata,ancestry=record
    current=_external_regular_binding(path,max(identity[6],1))
    return current is not None and current[0]==digest and current[1]==identity and current[3:]==(parent_metadata,ancestry)
def _external_directory_binding(path):
    fd=None
    try:
        fd,metadata,ancestry=_secure_parent(path)
        return (path,_authority_identity(metadata),ancestry)
    except OSError: return None
    finally:
        if fd is not None: os.close(fd)
def _external_directory_still_bound(record):
    path,identity,ancestry=record; current=_external_directory_binding(path)
    return current is not None and current==(path,identity,ancestry)
def _candidate_external_binding(candidate,*,observe_images):
    try:
        firmware=candidate["firmware"]
        files=[]
        for path,limit in ((Path(firmware["appPath"]),candidate_manifest.MAX_FIRMWARE_ARTIFACT_BYTES),(Path(firmware["evidenceManifestPath"]),candidate_manifest.MAX_FIRMWARE_MANIFEST_BYTES)):
            record=_external_regular_binding(path,limit)
            if record is None: return None
            files.append(record)
        directories=[]
        for repository in candidate["repositories"].values():
            record=_external_directory_binding(Path(repository["path"]))
            if record is None: return None
            directories.append(record)
        docker=Path(candidate["tools"]["docker"]["path"])
        images={}
        if observe_images:
            for reference in (candidate["images"]["lessonStudioBackend"]["reference"],candidate["images"]["lessonStudioWeb"]["reference"],candidate["database"]["engineImage"]):
                observed=candidate_manifest._docker_image_descriptor(reference,docker)
                if observed is None: return None
                images[reference]=observed
        return (tuple(files),tuple(directories),docker,images)
    except (KeyError,TypeError,ValueError): return None
def _candidate_external_still_bound(candidate,binding,validation_now):
    if binding is None: return False
    files,directories,docker,images=binding
    if not all(_external_regular_still_bound(record) for record in files): return False
    if not all(_external_directory_still_bound(record) for record in directories): return False
    try:
        if any(not candidate_manifest._repository_matches_candidate(Path(value["path"]),value) for value in candidate["repositories"].values()): return False
        if any(not _exact_equal(candidate_manifest._docker_image_descriptor(reference,docker),observed) for reference,observed in images.items()): return False
        validation_candidate=copy.deepcopy(candidate)
        validation_candidate.get("tools",{}).pop("physicalAdmission",None)
        return not candidate_manifest.validate_candidate(validation_candidate,now=validation_now)
    except (KeyError,OSError,RuntimeError,TypeError,ValueError): return False
def _output_absent(path):
    parent_fd=None
    try:
        parent_fd,metadata,ancestry=_secure_parent(path.parent)
        if metadata.st_uid!=OPERATOR_UID or not _parent_still_bound(path.parent,parent_fd,metadata,ancestry): return None
        try: os.stat(path.name,dir_fd=parent_fd,follow_symlinks=False)
        except FileNotFoundError: return (path,metadata,ancestry)
        return None
    except OSError: return None
    finally:
        if parent_fd is not None: os.close(parent_fd)
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

def _trusted_lsof():
    try:
        executable=TRUSTED_LSOF_EXECUTABLE
        if executable not in (Path("/usr/sbin/lsof"),Path("/usr/bin/lsof")) or executable.is_symlink(): return False
        tool=os.stat(executable,follow_symlinks=False)
        if not stat.S_ISREG(tool.st_mode) or tool.st_uid!=0 or tool.st_mode&0o022 or not tool.st_mode&0o111: return False
        for parent in (executable.parent,*executable.parent.parents):
            meta=os.stat(parent,follow_symlinks=False)
            if not stat.S_ISDIR(meta.st_mode) or meta.st_uid!=0 or meta.st_mode&0o022: return False
        return True
    except OSError: return False
def _device_lstat(path): return os.lstat(path)
def _device_stat(path): return os.stat(path)
def _enumerate_devices():
    identities={}
    for name in sorted(glob.glob("/dev/cu.usb*")):
        try: metadata=_device_lstat(name); resolved=_device_stat(name)
        except OSError: continue
        identity=lambda value:(value.st_dev,value.st_ino,value.st_rdev,value.st_mode)
        if stat.S_ISCHR(metadata.st_mode) and stat.S_ISCHR(resolved.st_mode) and identity(metadata)==identity(resolved): identities[name]=identity(metadata)
    return identities
def _run_lsof(command):
    try: process=subprocess.Popen(command,cwd="/",env={"PATH":"/usr/bin:/bin","LANG":"C","LC_ALL":"C","HOME":"/nonexistent"},stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
    except OSError: return None,b"",b"","not_found"
    assert process.stdout is not None and process.stderr is not None
    selector=selectors.DefaultSelector(); buffers={process.stdout:bytearray(),process.stderr:bytearray()}; deadline=time.monotonic()+LSOF_TIMEOUT_SECONDS; error=None
    try:
        for stream in buffers: os.set_blocking(stream.fileno(),False); selector.register(stream,selectors.EVENT_READ)
        while selector.get_map():
            if time.monotonic()>=deadline: error="timeout"; break
            for key,_ in selector.select(min(0.05,max(0.0,deadline-time.monotonic()))):
                chunk=os.read(key.fileobj.fileno(),65536)
                if not chunk: selector.unregister(key.fileobj); continue
                buffers[key.fileobj].extend(chunk)
                if sum(map(len,buffers.values()))>MAX_LSOF_OUTPUT_BYTES: error="output"; break
            if error: break
        if error:
            with contextlib.suppress(ProcessLookupError): os.killpg(process.pid,signal.SIGKILL)
        returncode=process.wait(timeout=1)
    except (OSError,subprocess.TimeoutExpired):
        with contextlib.suppress(ProcessLookupError): os.killpg(process.pid,signal.SIGKILL)
        with contextlib.suppress(Exception): process.wait(timeout=1)
        returncode=process.returncode; error=error or "run"
    finally:
        selector.close(); process.stdout.close(); process.stderr.close()
    return returncode,bytes(buffers[process.stdout]),bytes(buffers[process.stderr]),error

def collect_serial_inventory():
    before=_enumerate_devices(); devices=sorted(before)
    if not _trusted_lsof(): return devices,[],"untrusted"
    returncode,stdout,stderr,error=_run_lsof([str(TRUSTED_LSOF_EXECUTABLE),"-nP","-t","--",SERIAL_PATH])
    after=_enumerate_devices()
    if after!=before: return sorted(after),[],"inventory_changed"
    if error: return devices,[],error
    if returncode not in (0,1): return devices,[],"exit"
    if stderr.strip(): return devices,[],"visibility"
    holders=[]
    try: lines=stdout.decode("ascii",errors="strict").splitlines()
    except UnicodeError: return devices,[],"output"
    for line in lines:
        token=line.strip().removeprefix("p")
        if token.isdigit(): holders.append(int(token))
        elif token: return devices,[],"output"
    return devices,sorted(set(holders)),None

def _serial_inventory_safe():
    devices,holders,error=collect_serial_inventory()
    return error is None and devices==[SERIAL_PATH] and not holders

def _candidate_matches(binding, actual):
    if not isinstance(actual,dict): return False
    if any(not _exact_equal(actual.get(k),binding.get(k)) for k in ("candidateId","createdAt","expiresAt")): return False
    if not _exact_equal(actual.get("course"),{"courseId":binding.get("courseId"),"courseKey":binding.get("courseKey")}): return False
    repos=binding.get("repositories",{})
    if not _exact_equal(actual.get("repositories",{}).get("adminEsp"),repos.get("admin")) or not _exact_equal(actual.get("repositories",{}).get("backend"),repos.get("backend")) or not _exact_equal(actual.get("repositories",{}).get("firmware"),repos.get("firmware")): return False
    images=binding.get("images",{}); actual_images=actual.get("images",{})
    for signed_name,actual_name in (("backend","lessonStudioBackend"),("web","lessonStudioWeb")):
        if not isinstance(actual_images.get(actual_name),dict) or any(not _exact_equal(actual_images[actual_name].get(k),images.get(signed_name,{}).get(k)) for k in ("reference","id")): return False
    firmware=binding.get("firmware",{}); app=firmware.get("app",{}); manifest=firmware.get("manifest",{}); actual_firmware=actual.get("firmware",{})
    expected_fields={"appPath":app.get("path"),"appOffset":app.get("offset"),"appBytes":app.get("bytes"),"appSha256":app.get("sha256"),"partitionBytes":app.get("partitionBytes"),"evidenceManifestPath":manifest.get("path"),"evidenceManifestSha256":manifest.get("sha256")}
    return isinstance(actual_firmware,dict) and all(_exact_equal(actual_firmware.get(k),v) for k,v in expected_fields.items())
def _validate_candidate_shape(value,reasons):
    if not isinstance(value,dict) or set(value)!=CANDIDATE_KEYS: reasons.add("candidate.schema"); return
    if not _exact_equal(value.get("courseId"),COURSE_ID) or not _exact_equal(value.get("courseKey"),COURSE_KEY): reasons.add("candidate.course")
    repos=value.get("repositories")
    if not isinstance(repos,dict) or set(repos)!={"admin","backend","firmware"}: reasons.add("candidate.repositories"); repos={}
    for name,repo in repos.items():
        if not isinstance(repo,dict) or set(repo)!=REPOSITORY_KEYS or not _exact_equal(repo.get("dirtyExceptions"),[]): reasons.add(f"candidate.repositories.{name}")
    if isinstance(repos.get("firmware"),dict) and not _exact_equal(repos["firmware"].get("sha"),FIRMWARE_SHA): reasons.add("candidate.repositories.firmware")
    images=value.get("images")
    if not isinstance(images,dict) or set(images)!={"backend","web"}: reasons.add("candidate.images"); images={}
    for name,image in images.items():
        repo=repos.get("backend" if name=="backend" else "admin",{})
        labels={"org.opencontainers.image.revision":repo.get("sha"),"org.opencontainers.image.source":repo.get("remoteUrl")}
        if not isinstance(image,dict) or set(image)!=IMAGE_KEYS or not _exact_equal(image.get("platform"),"linux/arm64") or not _exact_equal(image.get("provenanceLabels"),labels): reasons.add(f"candidate.images.{name}")
    fw=value.get("firmware"); expected_app={"sha256":APP_SHA256,"bytes":APP_BYTES,"offset":APP_OFFSET,"partitionBytes":PARTITION_BYTES}
    if not isinstance(fw,dict) or set(fw)!={"board","target","gitSha","app","manifest"}: reasons.add("candidate.firmware"); return
    if not _exact_equal((fw.get("board"),fw.get("target"),fw.get("gitSha")),(BOARD,TARGET,FIRMWARE_SHA)): reasons.add("candidate.firmware")
    app=fw.get("app"); manifest=fw.get("manifest")
    if not isinstance(app,dict) or set(app)!={"path",*expected_app} or not Path(str(app.get("path",""))).is_absolute() or any(not _exact_equal(app.get(k),v) for k,v in expected_app.items()): reasons.add("candidate.firmware.app")
    if not isinstance(manifest,dict) or set(manifest)!={"path","sha256"} or not Path(str(manifest.get("path",""))).is_absolute() or not _exact_equal(manifest.get("sha256"),MANIFEST_SHA256): reasons.add("candidate.firmware.manifest")

def validate_documents(doc,identity,actual,now,devices,holders,inventory_error):
    reasons=set()
    if not isinstance(doc,dict) or set(doc)!=TOP_KEYS or type(doc.get("schemaVersion")) is not int or doc.get("schemaVersion")!=1: return ["input.schema"]
    if not isinstance(identity,dict) or set(identity)!=IDENTITY_KEYS or type(identity.get("schemaVersion")) is not int or identity.get("schemaVersion")!=1: return ["expectedIdentity.schema"]
    try: valid_uuid=str(uuid.UUID(doc.get("sessionId","")))==doc.get("sessionId")
    except (ValueError,TypeError,AttributeError): valid_uuid=False
    if not valid_uuid or doc.get("sessionId")!=identity.get("sessionId"): reasons.add("sessionId")
    checked=_parse_utc(doc.get("checkedAt"))
    if checked is None: reasons.add("checkedAt")
    elif checked>now: reasons.add("checkedAt.future")
    elif (now-checked).total_seconds()>CHECK_FRESHNESS_SECONDS: reasons.add("checkedAt.stale")
    binding=doc.get("candidate"); _validate_candidate_shape(binding,reasons)
    if not _exact_equal(binding,identity.get("candidate")): reasons.add("candidate.identity")
    created=_parse_utc(binding.get("createdAt")) if isinstance(binding,dict) else None; expires=_parse_utc(binding.get("expiresAt")) if isinstance(binding,dict) else None
    if created is None or expires is None or not(created<=now<expires): reasons.add("candidate.time")
    if checked is not None and (created is None or expires is None or not(created<=checked<expires)): reasons.add("checkedAt.candidateInterval")
    if isinstance(binding,dict) and not _candidate_matches(binding,actual): reasons.add("candidate.reference")
    if not _exact_equal(identity.get("partitionTable"),EXPECTED_PARTITIONS): reasons.add("expectedIdentity.partitionTable")
    if not _exact_equal(identity.get("signer"),{"algorithm":"ed25519","fingerprint":PINNED_APPROVAL_KEY_FINGERPRINT}): reasons.add("expectedIdentity.signer")
    expected_robot={"mac":ROBOT_MAC,"board":BOARD,"target":TARGET,"serialPath":SERIAL_PATH,"exactlyOneRobot":True}
    if not _exact_equal(doc.get("robot"),identity.get("robot")) or not _exact_equal(doc.get("robot"),expected_robot): reasons.add("robot.identity")
    expected_lease={"soleLeaseConfirmed":True,"competingProcessesStopped":True,"devicePath":SERIAL_PATH,"discoveredDevices":[SERIAL_PATH],"holderPids":[],"inventoryMethod":"lstat-glob-lsof-v1"}
    lease=doc.get("serialLease")
    if not isinstance(lease,dict) or set(lease)!=SERIAL_KEYS or not _exact_equal(lease,expected_lease): reasons.add("serialLease")
    if inventory_error: reasons.add(f"serial.lsof.{inventory_error}")
    if devices!=[SERIAL_PATH]: reasons.add("serial.inventory")
    if holders: reasons.add("serial.occupied")
    plan=doc.get("flashPlan"); operation={"operation":"write_flash","offset":APP_OFFSET,"imageSha256":APP_SHA256,"imageBytes":APP_BYTES,"after":"no-reset","eraseChip":False,"mergedImage":False}; protected=[p for p in EXPECTED_PARTITIONS if p["protected"]]
    if not isinstance(plan,dict) or set(plan)!=FLASH_KEYS: reasons.add("flashPlan.keys")
    else:
        if not isinstance(plan.get("operation"),dict) or set(plan["operation"])!=OPERATION_KEYS or not _exact_equal(plan["operation"],operation): reasons.add("flashPlan.operation")
        if not _exact_equal(plan.get("protectedPartitions"),protected): reasons.add("flashPlan.protectedPartitions")
        if plan.get("preserveProtectedPartitions") is not True: reasons.add("flashPlan.preserveProtectedPartitions")
    safety=doc.get("safety")
    if not isinstance(safety,dict) or set(safety)!=SAFETY_KEYS: reasons.add("safety.keys")
    else:
        for key in sorted(SAFETY_KEYS):
            if safety.get(key) is not True: reasons.add(f"safety.{key}")
    return sorted(reasons)

def _commit_time_reasons(doc,now):
    checked=_parse_utc(doc.get("checkedAt")); binding=doc.get("candidate",{})
    created=_parse_utc(binding.get("createdAt")); expires=_parse_utc(binding.get("expiresAt"))
    reasons=[]
    if checked is None or created is None or expires is None or not(created<=checked<=now<expires): reasons.append("candidate.time")
    if checked is None or checked>now or (now-checked).total_seconds()>CHECK_FRESHNESS_SECONDS: reasons.append("checkedAt.stale")
    return sorted(set(reasons))

def _failure(reasons): print(json.dumps({"schemaVersion":1,"validator":VALIDATOR,"status":"fail","reasons":sorted(set(reasons)),"physicalActionsPerformed":False,"serialOpened":False},sort_keys=True,separators=(",",":")))
def _publish(path,payload,output_binding,sources,commit_safe):
    if not path.is_absolute() or ".." in path.parts: return False
    data=(json.dumps(payload,sort_keys=True,separators=(",",":"),allow_nan=False)+"\n").encode()
    parent_fd=fd=None; created_identity=None; created_inode=None
    try:
        parent_fd,_,_=_secure_parent(path.parent)
        _,parent_metadata,ancestry=output_binding
        if os.fstat(parent_fd).st_uid!=OPERATOR_UID: return False
        if not _parent_still_bound(path.parent,parent_fd,parent_metadata,ancestry): return False
        try: os.stat(path.name,dir_fd=parent_fd,follow_symlinks=False)
        except FileNotFoundError: pass
        else: return False
        if not commit_safe(): raise OSError("commit authority changed")
        fd=os.open(path.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0),0o600,dir_fd=parent_fd)
        created_identity=_file_identity(os.fstat(fd))
        created_inode=created_identity[:2]
        view=memoryview(data)
        while view:
            written=os.write(fd,view)
            if written<=0: raise OSError("short write")
            view=view[written:]
        os.fchmod(fd,0o444)
        finalized=os.fstat(fd)
        if ((finalized.st_dev,finalized.st_ino)!=created_inode or not stat.S_ISREG(finalized.st_mode) or stat.S_IMODE(finalized.st_mode)!=0o444 or finalized.st_nlink!=1 or finalized.st_uid!=OPERATOR_UID or finalized.st_size!=len(data)): raise OSError("output finalize failed")
        created_identity=_file_identity(finalized)
        os.fsync(fd)
        if not commit_safe(): raise OSError("commit time changed")
        if _file_identity(os.fstat(fd))!=created_identity: raise OSError("output changed")
        current=os.stat(path.name,dir_fd=parent_fd,follow_symlinks=False)
        if _file_identity(current)!=created_identity or not _output_parent_still_bound(path.parent,parent_fd,parent_metadata,ancestry): raise OSError("output changed")
        closing_fd=fd
        os.close(closing_fd)
        fd=None
        os.fsync(parent_fd)
        if not commit_safe(): raise OSError("commit time changed")
        current=os.stat(path.name,dir_fd=parent_fd,follow_symlinks=False)
        if (_file_identity(current)!=created_identity or not _output_parent_still_bound(path.parent,parent_fd,parent_metadata,ancestry) or not all(_still_bound(record) for record in sources) or not commit_safe()): raise OSError("final binding changed")
        return True
    except OSError:
        if parent_fd is not None and created_inode is not None:
            with contextlib.suppress(OSError):
                current=os.stat(path.name,dir_fd=parent_fd,follow_symlinks=False)
                if (current.st_dev,current.st_ino)==created_inode:
                    os.unlink(path.name,dir_fd=parent_fd)
                    os.fsync(parent_fd)
        return False
    finally:
        if fd is not None:
            with contextlib.suppress(OSError): os.close(fd)
        if parent_fd is not None:
            with contextlib.suppress(OSError): os.close(parent_fd)
def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for flag in ("input","output","expected-identity","expected-identity-signature"): parser.add_argument("--"+flag,required=True,type=Path)
    args=parser.parse_args(argv)
    output_binding=_output_absent(args.output) if args.output.is_absolute() else None
    if output_binding is None: _failure(["output.path"]); return 1
    input_record,error=_secure_read(args.input,MAX_JSON_BYTES)
    if error or input_record is None: _failure(["input.unreadable"]); return 1
    raw=input_record[0]
    doc,error=_load_json(raw)
    if error: _failure([f"input.{error}"]); return 1
    if not isinstance(doc,dict) or set(doc)!=TOP_KEYS or type(doc.get("schemaVersion")) is not int or doc.get("schemaVersion")!=1:
        _failure(["input.schema"]); return 1
    identity_record,error=_secure_read(args.expected_identity,MAX_JSON_BYTES)
    if error or identity_record is None: _failure(["expectedIdentity.unreadable"]); return 1
    identity_raw=identity_record[0]
    identity,error=_load_json(identity_raw)
    if error: _failure([f"expectedIdentity.{error}"]); return 1
    signature_record,error=_secure_read(args.expected_identity_signature,256)
    if error or signature_record is None or len(signature_record[0])!=64: _failure(["expectedIdentity.signature"]); return 1
    signature=signature_record[0]
    valid,fingerprint=_verify_signature(_canonical_bytes(identity),signature)
    if not valid: _failure(["expectedIdentity.signature"]); return 1
    binding=doc.get("candidate") if isinstance(doc,dict) else None
    candidate_record,error=_secure_read(Path(binding.get("path","")),MAX_JSON_BYTES) if isinstance(binding,dict) else (None,"path")
    if error or candidate_record is None or hashlib.sha256(candidate_record[0]).hexdigest()!=binding.get("sha256"): _failure(["candidate.input"]); return 1
    candidate_raw=candidate_record[0]
    actual,error=_load_json(candidate_raw)
    if error: _failure(["candidate.input"]); return 1
    descriptor=actual.get("tools",{}).get("physicalAdmission") if isinstance(actual,dict) else None
    expected_paths={"input":args.input,"output":args.output,"expectedIdentity":args.expected_identity,"expectedIdentitySignature":args.expected_identity_signature}
    evidence_root=Path(actual.get("evidenceRoot","")) if isinstance(actual,dict) else Path("")
    if not isinstance(descriptor,dict) or set(descriptor)!=set(expected_paths) or any(Path(descriptor.get(k,""))!=v for k,v in expected_paths.items()) or not evidence_root.is_absolute() or any(not path.is_relative_to(evidence_root) for path in expected_paths.values()): _failure(["candidate.physicalAdmission"]); return 1
    verified_audit,audit_reasons=software_snapshot.verify_current_software_audit(Path(binding["path"]),evidence_root,preserved_roots=())
    if verified_audit is None: _failure(audit_reasons); return 1
    external_binding=_candidate_external_binding(actual,observe_images=False)
    if external_binding is None: _failure(["candidate.external"]); return 1
    now=utc_now(); reasons=[f"candidate.{r}" for r in candidate_manifest.validate_candidate(actual,now=now)]
    devices,holders,inventory_error=collect_serial_inventory(); reasons.extend(validate_documents(doc,identity,actual,now,devices,holders,inventory_error))
    if reasons: _failure(reasons); return 1
    if not _candidate_external_still_bound(actual,external_binding,now): _failure(["candidate.external.changed"]); return 1
    observed_binding=_candidate_external_binding(actual,observe_images=True)
    if observed_binding is None: _failure(["candidate.external.changed"]); return 1
    external_binding=(external_binding[0],external_binding[1],observed_binding[2],observed_binding[3])
    if not all(_still_bound(record) for record in (input_record,identity_record,signature_record,candidate_record)):
        _failure(["input.changed"]); return 1
    commit_reasons=_commit_time_reasons(doc,utc_now())
    if commit_reasons: _failure(commit_reasons); return 1
    final_devices,final_holders,final_inventory_error=collect_serial_inventory()
    final_inventory_reasons=[]
    if final_inventory_error: final_inventory_reasons.append(f"serial.lsof.{final_inventory_error}")
    if final_devices!=[SERIAL_PATH]: final_inventory_reasons.append("serial.inventory")
    if final_holders: final_inventory_reasons.append("serial.occupied")
    if final_inventory_reasons: _failure(final_inventory_reasons); return 1
    if not _candidate_external_still_bound(actual,external_binding,now): _failure(["candidate.external.changed"]); return 1
    payload={"schemaVersion":1,"validator":VALIDATOR,"status":"pass","reasons":[],"candidateId":binding["candidateId"],"sessionId":doc["sessionId"],"signerFingerprint":fingerprint,"physicalActionsPerformed":False,"serialOpened":False,"inputSha256":hashlib.sha256(raw).hexdigest(),"expectedIdentitySha256":hashlib.sha256(identity_raw).hexdigest(),"candidateSha256":hashlib.sha256(candidate_raw).hexdigest(),"softwareAuditSha256":verified_audit.audit_sha256,"softwareSnapshotId":verified_audit.snapshot_id,"robotMac":ROBOT_MAC,"serialPath":SERIAL_PATH,"firmwareSha":FIRMWARE_SHA,"appSha256":APP_SHA256,"manifestSha256":MANIFEST_SHA256}
    def commit_safe():
        current_audit,_audit_reasons=software_snapshot.verify_current_software_audit(Path(binding["path"]),evidence_root,preserved_roots=())
        return (
            current_audit is not None
            and current_audit.audit_identity==verified_audit.audit_identity
            and current_audit.audit_sha256==verified_audit.audit_sha256
            and current_audit.snapshot_id==verified_audit.snapshot_id
            and not _commit_time_reasons(doc,utc_now())
            and _candidate_external_still_bound(actual,external_binding,now)
            and _serial_inventory_safe()
        )
    if not _publish(args.output,payload,output_binding,(input_record,identity_record,signature_record,candidate_record),commit_safe): _failure(["output.path"]); return 1
    return 0
def _entrypoint():
    try: return main()
    except Exception:
        _failure(["internal"])
        return 1
if __name__=="__main__": raise SystemExit(_entrypoint())
