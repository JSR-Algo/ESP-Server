"""Staging continuity audit follows seven actual canonical media mounts."""
import hashlib
import json
import pytest
from tests.test_course_mode_software_evidence_audit import evidence_fixture, _rewrite_json, _run

LANE_REPORTS = ['02-runtime-assignment-new-rollback.json', '02-runtime-browser-after-assignment.json', '03-quick-gate.json', '04-full-gate.json', '05-live-db-gate.json']

@pytest.mark.parametrize('profile,count,field,accepted', [
 ('m1-staging',7,None,True),
 ('m1-staging',4,None,False),
 ('m1-staging',6,None,False),
 ('m1-staging',8,None,False),
 ('m1-staging',7,'allMountsCanonical',False),
 ('m1-staging',7,'allMountsExist',False),
 ('m1-staging',7,'allMountsReadOnly',False),
 ('m1-staging',7,'imagesMatchCandidate',False),
 ('m1-staging',7,'new-assignment',False),
 ('m1-staging',7,'rollback-assignment',False),
 ('m1-staging',7,'manual-recreate',False),
 ('production',4,None,True),
 ('production',7,None,False),
 (None,4,None,True),
 (None,7,None,False),
])
def test_staging_continuity_mount_contract(evidence_fixture,profile,count,field,accepted):
 candidate,evidence,output=evidence_fixture
 value=json.loads(candidate.read_text())
 if profile is not None:value['qualificationProfile']=profile
 _rewrite_json(candidate,value)
 if profile=='m1-staging':
  for name in LANE_REPORTS:
   lane_path=evidence/name;lane=json.loads(lane_path.read_text())
   lane['qualificationProfile']='m1-staging'
   lane['operatorAttestationSha256']=hashlib.sha256((evidence/'00-operator-attestation.json').read_bytes()).hexdigest()
   _rewrite_json(lane_path,lane)
 path=evidence/'02-runtime-continuity-inspection.json'
 continuity=json.loads(path.read_text());continuity['mountCount']=count
 if field in ('new-assignment','rollback-assignment'):
  continuity['assignmentFlags'][field.split('-')[0]]=True
 elif field=='manual-recreate':
  continuity['manualRecreateAfterRollback']=True
 elif field:continuity[field]=False
 _rewrite_json(path,continuity)
 result=_run(candidate,evidence,output)
 report=json.loads(result.stdout)
 if accepted:
  assert result.returncode==0,report['findings']
  assert report['status']=='pass'
 else:
  assert result.returncode!=0
  assert 'evidence.continuity' in report['findings']


@pytest.mark.parametrize('name', LANE_REPORTS)
@pytest.mark.parametrize('mutation', ['missing', 'production', 'unknown', 'missing-attestation', 'wrong-attestation'])
def test_staging_lane_profile_must_match_candidate(evidence_fixture, name, mutation):
 candidate,evidence,output=evidence_fixture
 value=json.loads(candidate.read_text());value['qualificationProfile']='m1-staging'
 _rewrite_json(candidate,value)
 path=evidence/'02-runtime-continuity-inspection.json'
 continuity=json.loads(path.read_text());continuity['mountCount']=7;_rewrite_json(path,continuity)
 for filename in LANE_REPORTS:
  path=evidence/filename;report=json.loads(path.read_text());report['qualificationProfile']='m1-staging'
  report['operatorAttestationSha256']=hashlib.sha256((evidence/'00-operator-attestation.json').read_bytes()).hexdigest()
  if filename==name:
   if mutation=='missing':report.pop('qualificationProfile')
   elif mutation=='missing-attestation':report.pop('operatorAttestationSha256')
   elif mutation=='wrong-attestation':report['operatorAttestationSha256']='0'*64
   else:report['qualificationProfile']=mutation
  _rewrite_json(path,report)
 result=_run(candidate,evidence,output);report=json.loads(result.stdout)
 expected='evidence.runtime' if name.startswith('02-') else {'03-quick-gate.json':'evidence.quick','04-full-gate.json':'evidence.full','05-live-db-gate.json':'evidence.live_db'}[name]
 assert result.returncode!=0 and expected in report['findings']


@pytest.mark.parametrize('name', LANE_REPORTS)
def test_production_rejects_staging_lane_receipt(evidence_fixture, name):
 candidate,evidence,output=evidence_fixture
 path=evidence/name;value=json.loads(path.read_text());value['qualificationProfile']='m1-staging';_rewrite_json(path,value)
 result=_run(candidate,evidence,output)
 assert result.returncode!=0
