import ast
import contextlib
import io
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock,patch
import corgi_auth_check as a

class CorgiAuthCheck(unittest.TestCase):
    def setUp(self):
        self.secret={'channel_id':a.CHANNEL_ID,'client_id':'client','client_secret':'SECRET_VALUE','refresh_token':'REFRESH_VALUE'}
        self.env=patch.dict(a.os.environ,{'YOUTUBE_KOREAN_OAUTH':json.dumps(self.secret)},clear=True)
        self.env.start();self.addCleanup(self.env.stop)
    def test_missing(self):
        with patch.dict(a.os.environ,{},clear=True),patch.object(a,'request_json') as call:
            with self.assertRaisesRegex(a.CheckFailed,'MISSING_OAUTH'):a.check()
            call.assert_not_called()
    def test_stored_mismatch(self):
        self.secret['channel_id']='wrong'
        with patch.dict(a.os.environ,{'YOUTUBE_KOREAN_OAUTH':json.dumps(self.secret)}),patch.object(a,'request_json') as call:
            with self.assertRaisesRegex(a.CheckFailed,'STORED_CHANNEL_MISMATCH'):a.check()
            call.assert_not_called()
    def test_current_channel_mismatch(self):
        with patch.object(a,'request_json',side_effect=[{'access_token':'TOKEN'}, {'items':[{'id':'wrong'}]}]):
            with self.assertRaisesRegex(a.CheckFailed,'AUTHENTICATED_CHANNEL_MISMATCH'):a.check()
    def test_success_only_two_fixed_endpoints(self):
        with patch.object(a,'request_json',side_effect=[{'access_token':'TOKEN'}, {'items':[{'id':a.CHANNEL_ID}]}]) as call:
            self.assertEqual(a.check(),a.CHANNEL_ID)
        self.assertEqual(call.call_count,2)
        self.assertEqual(call.call_args_list[0].args[0],'https://oauth2.googleapis.com/token')
        self.assertEqual(call.call_args_list[1].args[0],'https://www.googleapis.com/youtube/v3/channels?part=id&mine=true')
    def test_error_secrecy(self):
        output=io.StringIO()
        with patch.object(a.urllib.request,'build_opener') as opener,contextlib.redirect_stdout(output):
            opener.return_value.open.side_effect=RuntimeError('SECRET_VALUE REFRESH_VALUE TOKEN provider body')
            self.assertEqual(a.main(),1)
        self.assertEqual(output.getvalue(),'Corgi auth check: AUTH_SERVICE_ERROR\n')
    def test_redirect_rejected(self):
        with self.assertRaisesRegex(a.CheckFailed,'REDIRECT_BLOCKED'):
            a.NoRedirect().redirect_request(None,None,302,'redirect',{},'https://evil.example/')
        opener=MagicMock();response=opener.open.return_value.__enter__.return_value;response.read.return_value=b'{}'
        with patch.object(a.urllib.request,'build_opener',return_value=opener) as build:
            self.assertEqual(a.request_json('https://oauth2.googleapis.com/token'),{})
            build.assert_called_once_with(a.NoRedirect)
    def test_stdlib_only_no_publish_imports(self):
        tree=ast.parse(Path(a.__file__).read_text())
        imports=[]
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):imports.extend(x.name.split('.')[0] for x in node.names)
            if isinstance(node,ast.ImportFrom):imports.append(node.module.split('.')[0])
        self.assertEqual(set(imports),{'json','os','urllib'})
        self.assertNotIn('cloud_runner',Path(a.__file__).read_text())
    def test_auth_workflow_read_only_and_no_schedule(self):
        text=(Path(a.__file__).parent/'.github/workflows/corgi-auth-check.yml').read_text()
        self.assertIn('contents: read',text);self.assertNotIn('schedule:',text)
        self.assertNotIn('GITHUB_TOKEN:',text);self.assertNotIn('corgi_runner.py',text)
