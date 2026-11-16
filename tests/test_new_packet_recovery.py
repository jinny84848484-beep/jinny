import json,unittest,hashlib
from unittest.mock import patch
import new_packet_recovery_20261117 as r
class Packet(unittest.TestCase):
 def test_pinned_manifest_passes_existing_guards(self):
  raw=r.BUNDLE_PATH.read_bytes();self.assertEqual(hashlib.sha256(raw).hexdigest(),r.BUNDLE_HASH)
  b=json.loads(raw);self.assertFalse(b['media_ready']);self.assertEqual(b['images'],[])
  self.assertEqual(len(r.c.parse_research_manifest(json.dumps(b['manifest']),b['evidence'],set())['stories']),2)
 def test_bad_archive_blocks(self):
  with self.assertRaisesRegex(ValueError,'archive'):r.verify_inputs(b'wrong',{})
 def test_wrong_branch_no_read(self):
  with patch.dict(r.c.os.environ,{'GITHUB_REF':'refs/heads/other'}),patch.object(r.c,'journal_get') as read:
   with self.assertRaises(ValueError):r.recover(b'wrong')
   read.assert_not_called()
