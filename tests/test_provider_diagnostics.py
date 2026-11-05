import io,json,unittest,urllib.error
from unittest.mock import patch
import cloud_runner as c

class ProviderDiagnostics(unittest.TestCase):
    def test_whitelist_excludes_message_and_arbitrary_fields(self):
        raw=json.dumps({'error':{'code':'invalid_image_url','param':'input[0].content[1].image_url','message':'private prompt and https://private.example','request_id':'secret'}})
        self.assertEqual(c.safe_provider_error_fields(raw),'code=invalid_image_url parameter=input.image_url')
        for raw in ['secret',json.dumps({'error':{'code':['secret'],'param':'private'}}),json.dumps({'error':{'code':'secret','param':'https://private.example'}}),'{"error":null}']:
            self.assertEqual(c.safe_provider_error_fields(raw),'code=unknown parameter=unknown')

    def test_http_error_does_not_expose_provider_body(self):
        error=urllib.error.HTTPError('https://api.openai.com/v1/responses',400,'Bad Request',{},io.BytesIO(b'{"error":{"code":"invalid_value","param":"include","message":"private"}}'))
        with patch.object(c.urllib.request,'urlopen',side_effect=error):
            with self.assertRaisesRegex(RuntimeError,'api.openai.com HTTP 400 code=invalid_value parameter=include') as caught:c.http('https://api.openai.com/v1/responses',{})
        self.assertNotIn('private',str(caught.exception))

    def test_stage_is_fixed_label_without_prompt(self):
        with patch.dict(c.os.environ,{'OPENAI_API_KEY':'secret'}),patch.object(c,'http',side_effect=RuntimeError('HTTP 400')),patch('builtins.print') as output:
            for instructions,search,stage in [(c.RESEARCH_PROMPT,True,'research'),(c.REVIEW_PROMPT,True,'editorial_review'),(c.CORRECTION_PROMPT,False,'narration_correction'),(None,False,'draft')]:
                with self.assertRaises(RuntimeError):c.openai('private prompt',search,instructions=instructions)
                output.assert_called_with('OpenAI stage started: '+stage,flush=True)
