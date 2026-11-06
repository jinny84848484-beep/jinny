import base64
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import original_graphics as g
import staged_transfer as t
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_original_graphics import JOB, SPEC

class ApprovedPNG(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.job=copy.deepcopy(JOB)
        p=patch.object(g,'EDITORIAL_HASH',g.digest({k:v for k,v in self.job.items() if k!='id'}));p.start();self.addCleanup(p.stop)
        self.kw=dict(approved_story_hash=g.digest(self.job),production_date=g.DATE,graphics_spec=SPEC)
        self.manifest=g.build_original_graphics(self.job,self.root/'source',**self.kw)
        self.files={c['file']:base64.b64encode((self.root/'source'/c['file']).read_bytes()).decode() for c in self.manifest['cards']}
    def check(self,files=None,manifest=None):
        return g.validate_approved_graphics(self.job,manifest or self.manifest,self.root/'dest',files=self.files if files is None else files,**self.kw)
    def test_exact_bytes_without_renderer(self):
        with patch.object(g,'_image',side_effect=AssertionError('No regeneration')):
            self.assertTrue(self.check())
            self.assertTrue(g.validate_approved_graphics(self.job,self.manifest,self.root/'dest',**self.kw))
    def test_unknown_missing_traversal_invalid_bytes(self):
        for change in ('extra','missing','badbase64','changed'):
            files=copy.deepcopy(self.files);name=next(iter(files))
            if change=='extra':files['../evil']=files[name]
            if change=='missing':files.pop(name)
            if change=='badbase64':files[name]='!'
            if change=='changed':files[name]=base64.b64encode(b'wrong').decode()
            with self.assertRaises(Exception):self.check(files)
        self.assertFalse((self.root/'dest').exists())
    def test_symlink(self):
        (self.root/'dest').mkdir(); (self.root/'dest'/next(iter(self.files))).symlink_to(self.root/'source'/next(iter(self.files)))
        with self.assertRaises(ValueError):self.check()
    def test_manifest_text_and_scene_rebinding_rejected(self):
        for field,val in [('path','../x'),('scene_index',3),('classification','other')]:
            m=copy.deepcopy(self.manifest);m['cards'][0][field]=val
            with self.assertRaises(ValueError):self.check(manifest=m)
        m=copy.deepcopy(self.manifest);m['cards'][0]['labels'][0]['text']='changed'
        with self.assertRaises(ValueError):self.check(manifest=m)
    def test_size_and_format(self):
        files=copy.deepcopy(self.files);files[next(iter(files))]='a'*262145
        with self.assertRaises(ValueError):self.check(files)
        from PIL import Image
        import io,hashlib
        out=io.BytesIO();Image.new('RGB',(1,1)).save(out,format='PNG');raw=out.getvalue()
        files=copy.deepcopy(self.files);name=next(iter(files));files[name]=base64.b64encode(raw).decode()
        m=copy.deepcopy(self.manifest);m['cards'][0]['sha256']=hashlib.sha256(raw).hexdigest()
        with self.assertRaises(ValueError):self.check(files,m)

class LargerSession(unittest.TestCase):
    def test_explicit_additional_limit_and_legacy_default(self):
        key=Ed25519PrivateKey.generate();payload={'jobs':[{'data':'a'*300000}]}
        receiver=t.create_receiver('repo','1','1','2026-11-07',t.public(key),max_bytes=1048576)
        with self.assertRaises(ValueError):t.seal_batch(receiver.announcement,payload,key)
        sealed=t.seal_batch(receiver.announcement,payload,key,max_bytes=1048576)
        self.assertEqual(receiver.receive(sealed),payload)
        legacy=t.create_receiver('repo','2','1','2026-11-07',t.public(key))
        sealed=t.seal_batch(legacy.announcement,payload,key,max_bytes=1048576)
        with self.assertRaises(ValueError):legacy.receive(sealed)
        with self.assertRaises(ValueError):t.create_receiver('repo','3','1','2026-11-07',t.public(key),max_bytes=1048577)

class SessionReconciliation(unittest.TestCase):
    def setUp(self):
        import additional_driver as d
        self.d=d
        self.ledger={'days':{'2026-11-06':{'status':'complete'}},'publications':[{'id':'untouched'}]}
        d.register_session(self.ledger,'12',{'run_id':'12','run_attempt':'1','day':'2026-11-07','session_id':'old'},'2026-11-07','2026-11-07-extra-01','a'*64,completed_anchor_day='2026-11-06')
        self.old=copy.deepcopy(self.ledger['additional_sessions']['12'])
        self.proof={'run_id':'12','run_attempt':'1','head_sha':'b'*40,'conclusion':'failure','stage':'materialize_assets','session_hash':d.a.digest(self.old),'recovery_zip_sha256':'c'*64,'recovery_entries':[],'ledger_sha':'d'*40,'checked_at':'2026-11-06T15:20:00Z'}
    def resolve(self):
        return self.d.reconcile_preproduction_session(self.ledger,'12',self.proof,expected_session_hash=self.d.a.digest(self.old),expected_proof_hash=self.d.a.digest(self.proof))
    def test_preserves_history_and_allows_new_not_reused(self):
        record=self.resolve();self.assertTrue(self.d.verified_no_effects_session(record))
        self.assertEqual(record['identity'],self.old['identity']);self.assertEqual(self.ledger['publications'],[{'id':'untouched'}])
        self.d.register_session(self.ledger,'13',{'run_id':'13'},'2026-11-07','2026-11-07-extra-01','e'*64,completed_anchor_day='2026-11-06')
        with self.assertRaises(ValueError):self.d.register_session(self.ledger,'12',{'run_id':'12'},'2026-11-07','2026-11-07-extra-01','e'*64,completed_anchor_day='2026-11-06')
    def test_unknown_or_paid_stages_reject(self):
        for stage in ('production','unknown','session_completion'):
            self.proof['stage']=stage
            with self.assertRaises(ValueError):self.resolve()
        self.proof['stage']='materialize_assets';self.proof['recovery_entries']=['approved-input.json']
        with self.assertRaises(ValueError):self.resolve()
    def test_any_production_record_blocks(self):
        self.ledger['additional_batches']={'anything':{'status':'complete'}}
        with self.assertRaises(ValueError):self.resolve()
    def test_tampering_and_unverified_status_rejected(self):
        record=self.resolve();record['resolution']['proof']['head_sha']='f'*40
        self.assertFalse(self.d.verified_no_effects_session(record))
        with self.assertRaises(ValueError):self.d.register_session(self.ledger,'13',{'run_id':'13'},'2026-11-07','2026-11-07-extra-01','e'*64,completed_anchor_day='2026-11-06')

    def test_invalid_timestamp_rejected(self):
        for value in ('nonsense','2026-11-06T15:20:00'):
            self.proof['checked_at']=value
            with self.assertRaises(ValueError):self.resolve()
