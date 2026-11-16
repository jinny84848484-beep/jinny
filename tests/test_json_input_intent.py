import unittest
from unittest.mock import patch
import cloud_runner as c
class JSONIntent(unittest.TestCase):
 def test_json_mode_explicit_input(self):
  with patch.dict(c.os.environ,{'OPENAI_API_KEY':'fixture'}),patch.object(c,'http',return_value={'status':'completed','output':[]}) as http:
   c.openai('Prepare two stories',stage='draft')
   body=http.call_args.args[1]
   self.assertIn('JSON',body['input']);self.assertEqual(body['text']['format']['type'],'json_object')
 def test_existing_json_input_unchanged(self):
  with patch.dict(c.os.environ,{'OPENAI_API_KEY':'fixture'}),patch.object(c,'http',return_value={'status':'completed','output':[]}) as http:
   c.openai('Return JSON',stage='draft');self.assertEqual(http.call_args.args[1]['input'],'Return JSON')
