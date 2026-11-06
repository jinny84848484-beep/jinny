import copy, json, unittest
from unittest.mock import patch
import cloud_runner as c
import cached_review_recovery as r

class Selection(unittest.TestCase):
    def setUp(self):
        self.job={'id':'j','topic_key':'t','scenes':[{},{}]}
        self.media={'a':{'topic_key':'t'},'b':{'topic_key':'t'}}
        self.decision={'status':'PASS','issues':[],'selection':{'j':[{'scene_index':0,'image_id':'a'},{'scene_index':1,'image_id':'b'}]}}
    def parse(self,d):return c.parse_editorial_review(json.dumps(d),[self.job],self.media)
    def test_exact_objects_and_strings(self):
        self.assertEqual(self.parse(self.decision)['selection']['j'],['a','b'])
        d=copy.deepcopy(self.decision);d['selection']['j']=['a','b'];self.assertEqual(self.parse(d),d)
    def test_reject_ambiguous_objects(self):
        arrays=[[{'scene_index':1,'image_id':'a'},{'scene_index':0,'image_id':'b'}],
                [{'scene_index':0,'image_id':'a'},{'scene_index':0,'image_id':'b'}],
                [{'scene_index':False,'image_id':'a'},{'scene_index':1,'image_id':'b'}],
                [{'scene_index':0,'image_id':'a','extra':True},{'scene_index':1,'image_id':'b'}],
                [{'scene_index':0,'image_id':'a'},'b'],
                [{'scene_index':0,'image_id':'a'}],
                [{'scene_index':0,'image_id':'a'},{'scene_index':1,'image_id':'a'}],
                [{'scene_index':0,'image_id':'a'},{'scene_index':1,'image_id':'unknown'}]]
        for arr in arrays:
            with self.subTest(arr=arr):
                d=copy.deepcopy(self.decision);d['selection']['j']=arr
                with self.assertRaises(ValueError):self.parse(d)
    def test_no_decision_promotion(self):
        d=copy.deepcopy(self.decision);d['status']='FAIL'
        with self.assertRaises(ValueError):self.parse(d)
        self.media['b']['topic_key']='other'
        with self.assertRaises(ValueError):self.parse(self.decision)

class RecoveryTransport(unittest.TestCase):
    def test_bad_context_never_reads_journal(self):
        with patch.dict(r.os.environ,{},clear=True),patch.object(c,'journal_get',side_effect=AssertionError('must not read')):
            with self.assertRaises(RuntimeError):r.recover_from_archive(b'')
    def test_wrong_archive_and_day_never_reads_zip(self):
        for raw,day in ((b'bad',r.DAY),(b'', '2099-01-01')):
            with self.assertRaises(ValueError):r.inspect_archive(raw,{},day)
    def test_error_message_sanitized(self):
        with patch.object(r,'download_archive',side_effect=RuntimeError('secret signed URL')):
            with self.assertRaisesRegex(RuntimeError,'^Cached recovery stopped; reconcile journal, no automatic retry$'):
                r.run_recovery()
    def test_rejected_source_metadata(self):
        import urllib.request
        from unittest.mock import MagicMock
        response=MagicMock();response.__enter__.return_value.read.return_value=b'{}'
        opener=MagicMock();opener.open.return_value=response
        with patch.object(urllib.request,'build_opener',return_value=opener),patch.object(c,'gh_headers',return_value={}):
            with self.assertRaisesRegex(ValueError,'source run rejected'):r.download_archive()
        self.assertEqual(opener.open.call_count,1)
    def test_signed_download_does_not_forward_token(self):
        import io, hashlib, urllib.request, urllib.error
        from unittest.mock import MagicMock
        raw=b'synthetic zip bytes'
        run={'id':int(r.SOURCE_RUN),'run_attempt':1,'status':'completed','conclusion':'failure','head_sha':r.SOURCE_HEAD,'head_branch':'main','workflow_id':407256129,'path':'.github/workflows/shorts.yml','event':'workflow_dispatch','head_repository':{'full_name':r.REPOSITORY},'actor':{'id':341766743}}
        artifact={'id':r.ARTIFACT_ID,'expired':False,'name':'shorts-'+r.SOURCE_RUN,'workflow_run':{'id':int(r.SOURCE_RUN),'head_sha':r.SOURCE_HEAD},'size_in_bytes':34012,'digest':'sha256:'+r.ARCHIVE_SHA256}
        redirect=urllib.error.HTTPError('https://api.github.com/',302,'found',{'Location':'https://example.blob.core.windows.net/blob?signed=1'},None)
        opener=MagicMock();opener.open.side_effect=[io.BytesIO(json.dumps(run).encode()),io.BytesIO(json.dumps(artifact).encode()),redirect,io.BytesIO(raw)]
        artifact['digest']='sha256:'+hashlib.sha256(raw).hexdigest()
        opener.open.side_effect=[io.BytesIO(json.dumps(run).encode()),io.BytesIO(json.dumps(artifact).encode()),redirect,io.BytesIO(raw)]
        with patch.object(urllib.request,'build_opener',return_value=opener),patch.object(c,'gh_headers',return_value={'Authorization':'Bearer synthetic'}),patch.object(r,'ARCHIVE_SHA256',hashlib.sha256(raw).hexdigest()):
            self.assertEqual(r.download_archive(),raw)
        calls=opener.open.call_args_list
        self.assertEqual(calls[2].args[0].get_header('Authorization'),'Bearer synthetic')
        self.assertIsNone(calls[3].args[0].get_header('Authorization'))
    def test_foreign_redirect_rejected(self):
        import io, urllib.request, urllib.error
        from unittest.mock import MagicMock
        run={'id':int(r.SOURCE_RUN),'run_attempt':1,'status':'completed','conclusion':'failure','head_sha':r.SOURCE_HEAD,'head_branch':'main','workflow_id':407256129,'path':'.github/workflows/shorts.yml','event':'workflow_dispatch','head_repository':{'full_name':r.REPOSITORY},'actor':{'id':341766743}}
        artifact={'id':r.ARTIFACT_ID,'expired':False,'name':'shorts-'+r.SOURCE_RUN,'workflow_run':{'id':int(r.SOURCE_RUN),'head_sha':r.SOURCE_HEAD},'size_in_bytes':34012,'digest':'sha256:'+r.ARCHIVE_SHA256}
        opener=MagicMock();opener.open.side_effect=[io.BytesIO(json.dumps(run).encode()),io.BytesIO(json.dumps(artifact).encode()),urllib.error.HTTPError('https://api.github.com/',302,'found',{'Location':'https://evil.invalid/blob'},None)]
        with patch.object(urllib.request,'build_opener',return_value=opener),patch.object(c,'gh_headers',return_value={}):
            with self.assertRaisesRegex(ValueError,'redirect rejected'):r.download_archive()
        self.assertEqual(opener.open.call_count,3)
