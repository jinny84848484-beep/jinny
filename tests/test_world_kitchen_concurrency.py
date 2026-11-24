import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from test_world_kitchen import episode
from world_kitchen.runner import run


class KitchenLedgerConcurrency(unittest.TestCase):
    def test_second_date_does_not_disappear_when_first_renders_last(self):
        with TemporaryDirectory() as tmp:
            p=Path(tmp)
            m=p/'episode.json'
            m.write_text(json.dumps(episode()), encoding='utf-8')
            a,b='2026-11-24','2026-11-25'
            state=p/'state.json'
            queue=[dict(date=d, reviewed=True, manifest=str(m)) for d in (a,b)]
            reserved,release=Event(),Event()
            def slow(*_):
                reserved.set()
                if not release.wait(5):
                    raise RuntimeError('fixture timed out')
                return p/'a.mp4'
            def unexpected(*_):
                self.fail('duplicate renderer invoked')
            with ThreadPoolExecutor(max_workers=2) as workers:
                task=workers.submit(run,queue,state,p,a,slow)
                try:
                    self.assertTrue(reserved.wait(5))
                    duplicate=run(queue,state,p,a,unexpected)
                    self.assertEqual(duplicate['previous'],'render_started')
                    self.assertFalse(duplicate['uploaded'])
                    self.assertFalse(run(queue,state,p,b,lambda *_:p/'b.mp4')['uploaded'])
                finally:
                    release.set()
                self.assertFalse(task.result(timeout=5)['uploaded'])
            snapshot=json.loads(state.read_text(encoding='utf-8'))
            self.assertEqual(set(snapshot),{a,b})
            for d in (a,b):
                self.assertEqual(snapshot[d]['status'],'rendered_requires_visual_audio_and_caption_review')
                self.assertEqual(run(queue,state,p,d,unexpected)['status'],'already_attempted')

    def test_corrupt_ledger_is_not_replaced(self):
        with TemporaryDirectory() as tmp:
            p=Path(tmp)
            state=p/'state.json'
            state.write_text('{partial',encoding='utf-8')
            def unexpected(*_):
                self.fail('renderer invoked despite broken ledger')
            rows=[dict(date='2026-11-24',reviewed=True,manifest='unused')]
            with self.assertRaises(json.JSONDecodeError):
                run(rows,state,p,'2026-11-24',unexpected)
            self.assertEqual(state.read_text(encoding='utf-8'),'{partial')
