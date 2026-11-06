import sys,pathlib,tempfile,unittest,io,urllib.error,json
from unittest.mock import patch
import cloud_runner as c
class Download(unittest.TestCase):
 def invoke(self,response):
  with tempfile.TemporaryDirectory() as t:
   root=pathlib.Path(t);lib={'a':{'path':'assets/a.jpg','url':'https://upload.wikimedia.org/wikipedia/commons/a/a1/a.jpg','relevance_verified':True}}
   with patch.object(c.p,'ROOT',root),patch.object(c.p,'read',return_value=lib),patch.object(c.urllib.request,'urlopen',side_effect=response) as call:
    c.download_media([{'scenes':[{'media':'a'}]}]);self.assertEqual((root/'assets/a.jpg').read_bytes(),b'photo')
    self.assertEqual(call.call_args.args[0].get_header('User-agent'),'JinnyShorts/1.0 (https://github.com/jinny84848484-beep/jinny)')
 def test_normal_user_agent(self):self.invoke(lambda *a,**k:io.BytesIO(b'photo'))
 def test_http_body_hidden(self):
  with self.assertRaisesRegex(c.MediaDownloadError,'^Media download HTTP 403$'):
   self.invoke(urllib.error.HTTPError('secret',403,'sensitive',{},io.BytesIO(b'secret')))
 def test_network_hidden(self):
  with self.assertRaisesRegex(c.MediaDownloadError,'^Media download network failure$'):
   self.invoke(urllib.error.URLError('secret'))
if __name__=='__main__':unittest.main()
