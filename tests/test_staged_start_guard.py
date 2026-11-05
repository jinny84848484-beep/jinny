import ast, copy, json, unittest
from pathlib import Path
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock
from start_guard import assert_staged_start
DAY='2026-11-06'
def record():
    cp={'status':'completed','usable':True,'attempts':1,'request_hash':'a'*64,'sha256':'b'*64,'at':DAY+'T07:00:00+09:00','completed_at':DAY+'T07:01:00+09:00'}
    return {'status':'research_validation_failed','retry_hold':True,'active_stage':'research_repair','checkpoints':{'research':copy.deepcopy(cp),'research_repair':copy.deepcopy(cp)},'research_failures':[{'stage':'research_manifest','reason':'research_manifest_evidence_missing','repair_attempted':True,'at':DAY+'T07:02:00+09:00'}]}
class PureGuard(unittest.TestCase):
    def test_accept_unchanged(self):
        r=record(); before=copy.deepcopy(r);assert_staged_start(r,DAY,DAY);self.assertEqual(r,before)
    def test_single_research(self):
        r=record();del r['checkpoints']['research_repair'];r['active_stage']='research';r['research_failures'][-1]['repair_attempted']=False;assert_staged_start(r,DAY,DAY)
    def test_legacy_terminal_cases(self):
        for s in ('draft_validation_failed','research_completed_unusable','editorial_review_failed'):
            assert_staged_start({'status':s,'retry_hold':True},DAY,DAY)
    def test_rejections(self):
        mutations=[lambda r:r.update(status='complete'),lambda r:r.update(retry_hold=False),lambda r:r.update(jobs=[{}]),lambda r:r.update(staging={'id':'old'}),lambda r:r.update(upload_result={}),lambda r:r.update(active_job='old'),lambda r:r.update(review_input_hash='x'),lambda r:r.update(active_stage='draft'),lambda r:r.update(research_failures=[]),lambda r:r.update(checkpoints={}),lambda r:r['checkpoints']['research'].update(status='submitted'),lambda r:r['checkpoints']['research'].update(status='unknown'),lambda r:r['checkpoints']['research'].update(usable=False),lambda r:r['checkpoints']['research'].update(at='2026-11-05T07:00:00+09:00'),lambda r:r['checkpoints']['research'].update(at=DAY+'T07:00:00'),lambda r:r['checkpoints']['research'].update(sha256='wrong'),lambda r:r['checkpoints'].update(draft={'status':'completed'}),lambda r:r['research_failures'][-1].update(repair_attempted=False)]
        for i,mutate in enumerate(mutations):
            with self.subTest(case=i):
                r=record();mutate(r)
                with self.assertRaises(RuntimeError):assert_staged_start(r,DAY,DAY)
    def test_wrong_day(self):
        with self.assertRaises(RuntimeError):assert_staged_start(record(),DAY,'2026-11-07')
class Integration(unittest.TestCase):
    def setup_run(self,cas_fail=False,review_fail=False,midnight=False):
        ledger={'days':{DAY:record()},'publications':[]};durable=copy.deepcopy(ledger);calls=[]
        def get():return copy.deepcopy(durable),'sha'
        def put(value,sha):
            calls.append('cas')
            if cas_fail:raise RuntimeError('Conflict')
            durable.clear();durable.update(copy.deepcopy(value));return 'next'
        def review(ledger,sha,day,jobs,slots):
            calls.append('review')
            if review_fail:raise RuntimeError('Editorial review did not pass; batch held')
            return sha
        def publish(*a,**k):calls.append('publish')
        times=[datetime.fromisoformat(DAY+'T23:59:58+09:00')]*3
        if midnight:times.append(datetime.fromisoformat('2026-11-07T00:00:01+09:00'))
        def now():return times.pop(0) if len(times)>1 else times[0]
        c=SimpleNamespace(p=SimpleNamespace(now=now),journal_get=get,journal_put=put,preflight=lambda:calls.append('preflight'),review_jobs=review,publish_reviewed=publish)
        receiver=SimpleNamespace(announcement={'run_id':'1','run_attempt':'1','day':DAY,'session_id':'session'},deadline=100,receive=lambda e:{'jobs':[{'id':'job'}]},close=lambda:calls.append('closed'))
        ns={'c':c,'assert_staged_start':assert_staged_start,'run_context':lambda:('1','1','existing-public-key-placeholder'),'create_receiver':lambda *a:receiver,'REPOSITORY':'jinny84848484-beep/jinny','time':SimpleNamespace(monotonic=lambda:0,sleep=lambda n:None),'json':json,'fetch_envelope':lambda p:(b'opaque','blob'),'validate_staged':lambda *a:(('korean','science'),),'staged_failure_label':lambda *a:'held'}
        tree=ast.parse((Path(__file__).parents[1]/'staged_runner.py').read_text())
        nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('session_record','assert_waiting','run_staged')]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'candidate','exec'),ns)
        return ns['run_staged'],durable,calls
    def test_cas_stops_before_preflight(self):
        run,d,calls=self.setup_run(cas_fail=True)
        with self.assertRaises(RuntimeError):run()
        self.assertNotIn('preflight',calls);self.assertNotIn('review',calls);self.assertTrue(d['days'][DAY]['retry_hold'])
    def test_pass_calls_shared_review_then_publish(self):
        run,d,calls=self.setup_run();run();self.assertLess(calls.index('review'),calls.index('publish'));self.assertTrue(d['days'][DAY]['retry_hold']);self.assertEqual(len(d['days'][DAY]['research_failures']),1)
    def test_failed_review_never_publishes(self):
        run,d,calls=self.setup_run(review_fail=True)
        with self.assertRaises(RuntimeError):run()
        self.assertNotIn('publish',calls);self.assertTrue(d['days'][DAY]['retry_hold'])
    def test_midnight_before_preflight(self):
        run,d,calls=self.setup_run(midnight=True)
        with self.assertRaises(RuntimeError):run()
        self.assertNotIn('preflight',calls);self.assertNotIn('review',calls)
if __name__=='__main__':unittest.main()
