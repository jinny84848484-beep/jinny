import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import cloud_runner as c
import research_manifest_recovery as r


class PinnedResearchRecovery(unittest.TestCase):
    def setUp(self):
        self.ledger = json.loads((c.p.ROOT/'state/ledger.json').read_text())
        # Tests must remain valid after the real journal advances.
        self.record = {
            'checkpoints': {
                'research': {'status':'completed','request_hash':'3157653b696aaefc59fe30a4a2935fb5f93fb910713964a866bf9e925ce6f255','attempts':1,'at':'2026-11-14T11:08:01.799159+09:00','usable':True,'sha256':'d29e166702000de5f8f7985a699cc124b3ba68872d4c333f4940bea1f28b067a','completed_at':'2026-11-14T11:08:32.683119+09:00'},
                'research_repair': {'status':'completed','request_hash':'9cdbfe018586e21b70a2cbdaea68e45cd05641c827740c517152229a06534420','attempts':1,'at':'2026-11-14T11:08:33.854205+09:00','usable':True,'sha256':'0a9eeced7e7842c8a93b9fa4c04322d2e2fb96c01a4bb368028b7ed658b9e951','completed_at':'2026-11-14T11:09:07.014159+09:00'}},
            'status':'research_validation_failed','active_stage':'research_repair','retry_hold':True,
            'research_failures':[{'reason':'research_manifest_news_source','stage':'research_manifest','repair_attempted':True,'at':'2026-11-14T11:09:08.231260+09:00'}]}
        self.ledger['days'][r.DAY] = copy.deepcopy(self.record)

    def test_bundle_is_valid_but_does_not_certify_editorial_review(self):
        self.assertEqual(c.fingerprint(self.record), r.RECORD_HASH)
        b=r.load_bundle()
        m=c.parse_research_manifest(json.dumps(b['manifest']),b['evidence'],set())
        self.assertEqual(len(m['stories']),2)
        self.assertNotIn('PASS',json.dumps(b))
        self.assertNotIn('wikimedia',json.dumps([s['sources'] for s in m['stories']]))
        self.assertTrue(all(e['response_sha256'] for e in b['evidence']))

    def test_manifest_change_or_bad_marker_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'bundle.json';f.write_text('{}')
            with patch.object(r,'BUNDLE_PATH',f):
                with self.assertRaisesRegex(ValueError,'hash'):r.load_bundle()
        with self.assertRaisesRegex(ValueError,'marker'):r.recovered_manifest({},set())

    def test_duplicate_topic_remains_rejected(self):
        b=r.load_bundle(); key=b['manifest']['stories'][0]['topic_key']
        with self.assertRaisesRegex(ValueError,'topics'):
            r.recovered_manifest({'research_recovery':{'bundle_sha256':r.BUNDLE_SHA256,'source_run':r.SOURCE_RUN}},{('ojirap',key)})

    def test_wrong_archive_and_changed_hold_never_write_or_call_providers(self):
        with patch.object(c,'journal_put') as save,patch.object(c,'preflight') as pre,patch.object(c,'produce') as produce:
            with self.assertRaises(ValueError):r.recover_from_archive(b'bad archive')
            for call in (save,pre,produce):call.assert_not_called()

    def test_source_packet_binding_rejects_foreign_urls(self):
        with patch.object(c.p,'write') as write:
            with self.assertRaisesRegex(ValueError,'binding'):
                r.write_source_packets([{'id':'safe','sources':[{'url':'https://example.org/fake'}]}])
            write.assert_not_called()

    def exercise_recovery(self, mutate=None, dirty=False, write_error=False):
        import datetime, hashlib, io, zipfile
        ledger={'days':{r.DAY:copy.deepcopy(self.record),'old':{'status':'complete'}},'publications':[]}
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:
            for stage,entry in ledger['days'][r.DAY]['checkpoints'].items():
                raw=json.dumps({'day':r.DAY,'stage':stage,'request_hash':entry['request_hash'],'result':{'text':'original invalid research','sources':[]}}).encode()
                entry['sha256']=hashlib.sha256(raw).hexdigest()
                z.writestr(r.DAY+'/checkpoints/'+stage+'.json',raw)
            z.writestr(r.DAY+'/checkpoints/draft.json','test fixture never restored')
        raw=stream.getvalue();expected=c.fingerprint(ledger['days'][r.DAY])
        before=copy.deepcopy(ledger)
        if mutate:mutate(ledger)
        with tempfile.TemporaryDirectory() as d,patch.object(c.p,'ROOT',Path(d)),patch.object(c.p,'now',return_value=datetime.datetime(2026,11,14,tzinfo=c.p.KST)),patch.object(r,'ARCHIVE_SHA256',hashlib.sha256(raw).hexdigest()),patch.object(r,'RECORD_HASH',expected),patch.object(c,'journal_get',return_value=(ledger,'old-sha')),patch.object(c,'journal_put',side_effect=RuntimeError('CAS conflict') if write_error else None) as save,patch.object(c,'preflight') as pre,patch.object(c,'produce') as produce:
            if dirty:(Path(d)/'runs'/r.DAY/'checkpoints').mkdir(parents=True)
            if mutate or dirty or write_error:
                with self.assertRaises((ValueError,RuntimeError)):r.recover_from_archive(raw)
                produce.assert_not_called()
                if not write_error:save.assert_not_called()
                return
            r.recover_from_archive(raw)
            produce.assert_called_once();save.assert_called_once();pre.assert_called_once()
            current=ledger['days'][r.DAY]
            self.assertEqual(current['research_recovery']['previous_record'],before['days'][r.DAY])
            self.assertEqual(current['checkpoints'],before['days'][r.DAY]['checkpoints'])
            self.assertEqual(current['research_failures'],before['days'][r.DAY]['research_failures'])
            self.assertEqual(ledger['days']['old'],before['days']['old'])
            self.assertFalse((Path(d)/'runs'/r.DAY/'checkpoints/draft.json').exists())
            self.assertTrue((Path(d)/'runs'/r.DAY/'recovery/originals/research.json').exists())
            # A rerun cannot spend again after the one permitted transition.
            with self.assertRaisesRegex(ValueError,'exact'):r.recover_from_archive(raw)
            produce.assert_called_once()

    def test_recovery_preserves_originals_and_never_imports_fixture_pass(self):
        self.exercise_recovery()

    def test_stale_hold_pending_stage_existing_upload_and_dirty_files_fail_closed(self):
        for mutate in [lambda x:x['days'][r.DAY].update(retry_hold=False),lambda x:x['days'][r.DAY]['checkpoints'].update(draft={'status':'submitted'}),lambda x:x['publications'].append({'job_id':r.DAY+'-existing'})]:
            self.exercise_recovery(mutate=mutate)
        self.exercise_recovery(dirty=True)
        self.exercise_recovery(write_error=True)
