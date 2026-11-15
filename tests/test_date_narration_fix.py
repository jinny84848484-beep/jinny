import copy,unittest
from unittest.mock import patch
import date_narration_fix as d
class DateFix(unittest.TestCase):
 def fixture(self):
  return {'jobs':[{'id':d.JOB,'channel':'ojirap','scenes':[{}, {}, {'headline':'판매 시작은 십이월 십일','body':'한국과 미국은 같은 날 예정됐다.','narration':'판매 예정일이 십이월 십일입니다. 사전판매는 십일월 십이일입니다.'}],'fact_packet':{'verified_facts':['2026년 12월 10일 출시 예정']}},{'id':'2026-11-15-asean-week-seoul','channel':'ojirap','scenes':[{'narration':'unchanged'}]}],'images':{}}
 def test_exactly_three_date_fields_changed_original_and_other_job_preserved(self):
  saved=self.fixture();before=copy.deepcopy(saved)
  with patch.object(d,'INPUT_HASH',d.fingerprint(saved)):out=d.corrected_request(saved)
  self.assertEqual(saved,before);self.assertEqual(out['jobs'][1],saved['jobs'][1]);self.assertEqual(out['jobs'][0]['fact_packet'],saved['jobs'][0]['fact_packet'])
  s=out['jobs'][0]['scenes'][2];self.assertIn('2026년 12월 10일',s['headline']);self.assertIn('십이월 열흘',s['narration']);self.assertNotIn('십이월 십일',s['narration']);self.assertIn('십일월 십이일',s['narration'])
 def test_wrong_input_rejected(self):
  with self.assertRaises(ValueError):d.corrected_request(self.fixture())
 def test_scope_wrong_rejected_even_if_test_hash_matches(self):
  saved=self.fixture();saved['jobs'][0]['channel']='korean'
  with patch.object(d,'INPUT_HASH',d.fingerprint(saved)):
   with self.assertRaisesRegex(ValueError,'scope'):d.corrected_request(saved)
