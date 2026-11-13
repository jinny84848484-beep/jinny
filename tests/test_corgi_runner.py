import copy
import datetime as dt
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import corgi_runner as r

class Corgi(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'corgi').mkdir()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(patch.stopall)
        patch.object(r,'ROOT',self.root).start()
        self.day='2026-11-14'
        self.item={'id':'corgi-01','publish_date_kst':self.day,'channel_id':r.CHANNEL,'asset':'corgi/video.mp4','sha256':hashlib.sha256(b'mp4').hexdigest(),'metadata':{'title':'Corgi','description':'Original','made_for_kids':False,'contains_synthetic_media':True}}
        (self.root/self.item['asset']).write_bytes(b'mp4')
        for kind in ('qa','rights'):
            path='corgi/'+kind+'.json';raw=json.dumps({'approved':True,'asset_sha256':self.item['sha256'],'reviewer':'owner','evidence':'test evidence'}).encode()
            (self.root/path).write_bytes(raw);self.item[kind]={'path':path,'sha256':hashlib.sha256(raw).hexdigest()}
        self.item['approval']={'decision':'public','owner_approval_reference':'owner message test','manifest_sha256':r.digest(self.item)}
        self.queue()
    def queue(self):
        (self.root/'corgi/queue.json').write_text(json.dumps({'schema_version':1,'items':[self.item]}))
    def test_valid_and_tamper(self):
        r.approved_item(self.day)
        self.item['metadata']['title']='changed';self.queue()
        with self.assertRaisesRegex(ValueError,'stale'):r.approved_item(self.day)
    def test_empty_and_escape(self):
        with self.assertRaisesRegex(ValueError,'exactly one'):r.approved_item('2026-11-15')
        self.item['asset']='../outside';self.queue()
        with self.assertRaises(ValueError):r.approved_item(self.day)
    def runtime(self,ledger):
        now=patch.object(r.dt,'datetime').start()
        class Clock:
            hour=19
            def date(self):return dt.date(2026,11,14)
            def isoformat(self):return '2026-11-14T19:00:00+09:00'
        now.now.return_value=Clock()
        patch.dict(r.os.environ,{'GITHUB_REF':'refs/heads/main','CORGI_MODE':'publish'}).start()
        patch.object(r.c,'journal_get',return_value=(ledger,'sha')).start()
        patch.object(r.c,'youtube_token',return_value='token').start()
        patch.object(r.c,'verify_channel',return_value=r.CHANNEL).start()
    def test_durable_write_failure_prevents_upload(self):
        self.runtime({})
        with patch.object(r.c,'journal_put',side_effect=RuntimeError('conflict')),patch.object(r,'upload') as upload:
            with self.assertRaises(RuntimeError):r.run()
            upload.assert_not_called()
    def test_uncertain_no_retry(self):
        self.runtime({'corgi_publications':{'old':{'status':'upload_submitted'}}})
        with patch.object(r,'upload') as upload:
            with self.assertRaisesRegex(RuntimeError,'Uncertain'):r.run()
            upload.assert_not_called()
    def test_publish_and_verify_preserves_other_ledger(self):
        ledger={'days':{'old':{'status':'complete'}}};self.runtime(ledger);writes=[]
        def save(value,sha):writes.append(copy.deepcopy(value));return 'newsha'
        with patch.object(r.c,'journal_put',side_effect=save),patch.object(r,'upload',return_value='abcdefghijk'),patch.object(r.c,'http',return_value={'items':[{'id':'abcdefghijk','snippet':{'channelId':r.CHANNEL},'status':{'privacyStatus':'public'}}]}):r.run()
        self.assertEqual([x['corgi_publications']['corgi-01']['status'] for x in writes],['upload_submitted','verification_pending','complete'])
        self.assertEqual(ledger['days'],{'old':{'status':'complete'}})
    def test_wrong_channel_before_submission(self):
        self.runtime({})
        with patch.object(r.c,'verify_channel',return_value='wrong'),patch.object(r.c,'journal_put') as save,patch.object(r,'upload') as upload:
            with self.assertRaises(RuntimeError):r.run()
            save.assert_not_called();upload.assert_not_called()
    def test_network_failure_leaves_pending(self):
        ledger={};self.runtime(ledger)
        with patch.object(r.c,'journal_put',return_value='new'),patch.object(r,'upload',side_effect=RuntimeError('unknown')):
            with self.assertRaises(RuntimeError):r.run()
        self.assertEqual(ledger['corgi_publications']['corgi-01']['status'],'upload_submitted')

    def test_private_response_is_not_complete(self):
        ledger={};self.runtime(ledger)
        with patch.object(r.c,'journal_put',return_value='new'),patch.object(r,'upload',return_value='abcdefghijk'),patch.object(r.c,'http',return_value={'items':[{'id':'abcdefghijk','snippet':{'channelId':r.CHANNEL},'status':{'privacyStatus':'private'}}]}):
            with self.assertRaisesRegex(RuntimeError,'verification incomplete'):r.run()
        self.assertEqual(ledger['corgi_publications']['corgi-01']['status'],'verification_pending')

    def test_empty_queue_blocks_oauth(self):
        self.runtime({});(self.root/'corgi/queue.json').write_text('{"schema_version":1,"items":[]}')
        with patch.object(r.c,'youtube_token') as token,patch.object(r,'upload') as upload:
            with self.assertRaises(ValueError):r.run()
            token.assert_not_called();upload.assert_not_called()

    def test_resumable_adapter(self):
        from unittest.mock import MagicMock
        response=MagicMock();response.__enter__.return_value.headers={'Location':'https://www.googleapis.com/upload/session'}
        with patch.object(r.urllib.request,'urlopen',return_value=response) as start,patch.object(r.c,'http',return_value={'id':'abcdefghijk'}) as send:
            self.assertEqual(r.upload(self.item,self.root/self.item['asset'],'test-token'),'abcdefghijk')
            self.assertEqual(start.call_args.args[0].method,'POST')
            self.assertEqual(send.call_args.args[1],b'mp4')
            self.assertEqual(send.call_args.args[3],'PUT')

    def test_resumable_host_rejected_before_send(self):
        from unittest.mock import MagicMock
        response=MagicMock();response.__enter__.return_value.headers={'Location':'https://evil.example/upload'}
        with patch.object(r.urllib.request,'urlopen',return_value=response),patch.object(r.c,'http') as send:
            with self.assertRaisesRegex(RuntimeError,'endpoint'):r.upload(self.item,self.root/self.item['asset'],'test-token')
            send.assert_not_called()

    def test_completed_duplicates_block_oauth(self):
        for record in ({'date_kst':self.day},{'asset_sha256':self.item['sha256']}):
            record['status']='complete';self.runtime({'corgi_publications':{'previous':record}})
            with patch.object(r.c,'youtube_token') as token:
                with self.assertRaisesRegex(RuntimeError,'Duplicate'):r.run()
                token.assert_not_called()

    def test_wrong_video_channel_not_complete(self):
        ledger={};self.runtime(ledger)
        with patch.object(r.c,'journal_put',return_value='new'),patch.object(r,'upload',return_value='abcdefghijk'),patch.object(r.c,'http',return_value={'items':[{'id':'abcdefghijk','snippet':{'channelId':'other'},'status':{'privacyStatus':'public'}}]}):
            with self.assertRaisesRegex(RuntimeError,'verification incomplete'):r.run()
        self.assertEqual(ledger['corgi_publications']['corgi-01']['status'],'verification_pending')
