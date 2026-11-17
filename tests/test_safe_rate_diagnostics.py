import io,json,unittest,urllib.error
from email.message import Message
from unittest.mock import patch
import cloud_runner as c

class SafeRateDiagnostics(unittest.TestCase):
    def headers(self, values):
        result=Message()
        for key,value in values.items():result[key]=value
        return result

    def test_known_type_and_code_only(self):
        raw=json.dumps({'error':{'code':'slow_down','type':'rate_limit_error',
            'param':None,'message':'SECRET','request_id':'SECRET','token':'SECRET'}})
        self.assertEqual(c.safe_provider_error_fields(raw),
            'code=slow_down parameter=unknown type=rate_limit_error')
        self.assertNotIn('SECRET',c.safe_provider_error_fields(json.dumps(
            {'error':{'code':'SECRET','type':'SECRET','param':'SECRET'}})))

    def test_only_bounded_numeric_duration_headers(self):
        result=c.safe_provider_error_headers(self.headers({
            'Retry-After':'1.25','X-RateLimit-Limit-Tokens':'0200000',
            'X-RateLimit-Remaining-Requests':'0','X-RateLimit-Reset-Tokens':'1m2.5s',
            'X-RateLimit-Reset-Project-Tokens':'250ms','X-Request-ID':'SECRET',
            'Authorization':'SECRET','Set-Cookie':'SECRET'}))
        self.assertIn('retry-after=1.25',result)
        self.assertIn('x-ratelimit-limit-tokens=200000',result)
        self.assertIn('x-ratelimit-reset-tokens=1m2.5s',result)
        self.assertIn('x-ratelimit-reset-project-tokens=250ms',result)
        self.assertNotIn('SECRET',result)

    def test_malformed_or_sensitive_values_omitted(self):
        for value in ('SECRET','https://private','Bearer SECRET','1\nSECRET','9'*80,
                      '-1','nan','inf','Wed, 17 Nov 2026 00:00:00 GMT',''):
            self.assertEqual(c.safe_provider_error_headers(self.headers({
                'retry-after':value,'x-ratelimit-limit-tokens':value,
                'x-ratelimit-reset-tokens':value})), '')
        self.assertEqual(c.safe_provider_error_headers(None),'')

    def test_all_4xx_one_call_and_no_journal_writes(self):
        for status in range(400,500):
            error=urllib.error.HTTPError('https://api.openai.com/v1/responses',status,
                'SECRET',self.headers({'Retry-After':'2','X-Request-ID':'SECRET'}),
                io.BytesIO(b'{"error":{"code":"slow_down","type":"rate_limit_error","message":"SECRET"}}'))
            with patch.object(c.urllib.request,'urlopen',side_effect=error) as call, \
                 patch.object(c,'journal_put') as journal:
                with self.assertRaises(RuntimeError) as caught:
                    c.http('https://api.openai.com/v1/responses',{'input':'SECRET'})
                call.assert_called_once();journal.assert_not_called()
                self.assertNotIn('SECRET',str(caught.exception))
                self.assertIn('retry-after=2',str(caught.exception))

    def test_other_provider_headers_and_body_not_logged(self):
        error=urllib.error.HTTPError('https://example.com',429,'SECRET',
            self.headers({'Retry-After':'2'}),io.BytesIO(b'SECRET'))
        with patch.object(c.urllib.request,'urlopen',side_effect=error) as call:
            with self.assertRaises(RuntimeError) as caught:c.http('https://example.com')
            self.assertEqual(str(caught.exception),'example.com HTTP 429')
            call.assert_called_once()
