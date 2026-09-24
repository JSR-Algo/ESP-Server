"""Staging continuity audit follows seven actual canonical media mounts."""
import json
import pytest
from tests.test_course_mode_software_evidence_audit import evidence_fixture, _rewrite_json, _run

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
