import copy
import os
import json
from pathlib import Path
import tempfile
import unittest
import original_graphics as g

from unittest.mock import patch
JOB = {'id': '2026-11-06-neutral-test', 'channel': 'test', 'title': 'Neutral layout test',
       'scenes': [{'kind': 'fact', 'headline': 'Layout', 'narration': 'Neutral synthetic narration for renderer testing.'} for _ in range(5)]}
SPEC = {'cards': [['보도 사실' if i < 4 else '채널 논평', 'Neutral layout test', ['First neutral line', 'Second neutral line'], 'Synthetic test only'] for i in range(5)]}

class OriginalGraphicsTests(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault('SHORTS_STATE_DIR', str(Path(tempfile.gettempdir())/'graphics-test-state'))
        os.environ.setdefault('XDG_CACHE_HOME', str(Path(tempfile.gettempdir())/'graphics-fontcache'))
        self.job = copy.deepcopy(JOB)
        pin = g.digest({k:v for k,v in self.job.items() if k != 'id'})
        patcher = patch.object(g, 'EDITORIAL_HASH', pin)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.kw = dict(approved_story_hash=g.digest(self.job), production_date=g.DATE, graphics_spec=SPEC)
        self.manifest = g.build_original_graphics(self.job, self.root, **self.kw)

    def test_deterministic_and_safe(self):
        again = g.build_original_graphics(self.job, self.root, **self.kw)
        self.assertEqual(again, self.manifest)
        self.assertTrue(g.validate_original_graphics(self.job, again, self.root, **self.kw))
        for card in again['cards']:
            self.assertFalse(Path(card['path']).is_absolute())
            self.assertIn(g.NOTICE, [x['text'] for x in card['labels']])
            for label in card['labels']:
                x,y,r,b = label['bbox']
                self.assertGreaterEqual(label['font_size'],26)
                self.assertTrue(80 <= x < r <= 970 and 110 <= y < b <= 1070)
        self.assertEqual(again['cards'][-1]['classification'], '채널 논평')

    def test_invalid_specs(self):
        variants=[]
        for value in ['line\nbreak', 'x'*71, 'bad\u200btext']:
            spec=copy.deepcopy(SPEC);spec['cards'][0][1]=value;variants.append(spec)
        spec=copy.deepcopy(SPEC);spec['unknown']=1;variants.append(spec)
        spec=copy.deepcopy(SPEC);spec['cards'][0][2]=[];variants.append(spec)
        spec=copy.deepcopy(SPEC);spec['cards'][0][0]='채널 논평';variants.append(spec)
        for spec in variants:
            with self.assertRaises(ValueError): g.generate_original_graphics(self.job,self.root,graphics_spec=spec)

    def test_missing_and_foreign(self):
        path=self.root/self.manifest['cards'][0]['file']
        path.unlink()
        with self.assertRaises(ValueError): g.validate_original_graphics(self.job,self.manifest,self.root,**self.kw)
        path.write_bytes(b'foreign')
        with self.assertRaises(ValueError): g.validate_original_graphics(self.job,self.manifest,self.root,**self.kw)

    def test_rehashing_foreign_artifact_does_not_pass(self):
        m=copy.deepcopy(self.manifest)
        path=self.root/m['cards'][0]['file']; path.write_bytes(b'foreign')
        m['cards'][0]['sha256']=g.hashlib.sha256(b'foreign').hexdigest()
        with self.assertRaises(ValueError): g.validate_original_graphics(self.job,m,self.root,**self.kw)

    def test_content_and_date_binding(self):
        changed=copy.deepcopy(self.job); changed['scenes'][0]['narration']+=' 다른 사건'
        with self.assertRaises(ValueError): g.build_original_graphics(changed,self.root,**self.kw)
        with self.assertRaises(ValueError): g.build_original_graphics(changed,self.root,approved_story_hash=g.digest(changed),production_date=g.DATE, graphics_spec=SPEC)
        with self.assertRaises(ValueError): g.build_original_graphics(self.job,self.root,approved_story_hash=g.digest(self.job),production_date='2026-11-07',graphics_spec=SPEC)

    def test_runtime_id_allowed_but_bound(self):
        job=copy.deepcopy(self.job);job['id']=g.DATE+'-extra-01-stalking';job['channel_id']='approved-channel'
        manifest=g.generate_original_graphics(job,self.root,graphics_spec=SPEC)
        self.assertEqual(manifest['job_content_hash'],g.digest(job))
        with self.assertRaises(ValueError):g.validate_original_graphics(self.job,manifest,self.root,**self.kw)

    def test_swapped_scene_and_metadata_fail(self):
        for key,value in [('scene_index',4),('notice','news footage'),('path','../other.png'),('content_hash','0'*64)]:
            m=copy.deepcopy(self.manifest);m['cards'][0][key]=value
            with self.assertRaises(ValueError):g.validate_original_graphics(self.job,m,self.root,**self.kw)

if __name__=='__main__':unittest.main()
