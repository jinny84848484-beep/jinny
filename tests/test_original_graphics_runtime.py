import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import additional_batch as a
import additional_runtime as ar
import original_graphics as g


class GraphicsRuntimeIntegration(unittest.TestCase):
    def test_approved_graphics_roundtrip_review_is_distinct_from_photos(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            job = {'id': '2026-11-06-extra-01-neutral-test', 'channel': 'ojirap',
                   'channel_id': ar.c.p.cfg()['channels']['ojirap']['channel_id'], 'title': 'Neutral test',
                   'description': 'Neutral layout example', 'category': 'society', 'topic_key': 'neutral-test',
                   'sources': [{'title': 'A', 'url': 'https://a.example/article'},
                               {'title': 'B', 'url': 'https://b.example/article'}],
                   'scenes': [{'kind': 'fact' if i < 4 else 'closing', 'headline': 'Neutral', 'body': '',
                               'narration': '가' * 50, 'source_indices': [0, 1] if i < 4 else []} for i in range(5)]}
            spec = {'cards': [['보도 사실' if i < 4 else '채널 논평', 'Neutral test', ['Example line'], 'Test only'] for i in range(5)]}
            pin = g.digest({k: v for k, v in job.items() if k not in ('id', 'channel_id')})
            batch = '2026-11-06-extra-01'
            with patch.object(g, 'EDITORIAL_HASH', pin), patch.object(ar.c.p, 'ROOT', root):
                manifest = g.build_original_graphics(job, root/'assets'/batch,
                    approved_story_hash=a.digest(job), production_date='2026-11-06', graphics_spec=spec)
                packet = {'production_date': '2026-11-06', 'batch_id': batch, 'job': job,
                          'graphics_spec': spec, 'graphics_manifest': manifest, 'media_policy': 'original_graphics',
                          'media': ar.Runtime.graphics_media(job, batch, manifest)}
                import base64
                packet['graphics_files'] = {card['file']: base64.b64encode((root/'assets'/batch/card['file']).read_bytes()).decode() for card in manifest['cards']}
                runtime = ar.Runtime('a' * 40)
                runtime.bind_authorization({'config_hash': a.digest(runtime.config())}, packet)
                # The compositor has separate real-render smoke coverage; no FFmpeg needed here.
                frame_report = {'frames': [{'scene_index': i, 'path': card['path'], 'sha256': card['sha256']}
                                           for i, card in enumerate(manifest['cards'])]}
                with patch.object(g, 'render_graphics_review_frames', return_value=frame_report):
                    runtime.materialize_media(packet)
                runtime.validate_media(packet)
                request = runtime.review_request(packet, a.MODEL, 2600, 3)
                self.assertIn('ORIGINAL GRAPHICS policy', request['instructions'])
                self.assertNotIn('Fail if two usable directly relevant photos are absent', request['instructions'])
                self.assertNotIn('exact pictured-subject relevance', request['instructions'])
                text_part = request['input'][0]['content'][0]['text']
                review_text_packet = json.loads(text_part)
                self.assertNotIn('graphics_files', review_text_packet)
                self.assertIn('graphics_files', packet)  # immutable authorized packet
                self.assertEqual(review_text_packet['review_image_context']['height'], 1920)
                self.assertEqual(len([v for v in request['input'][0]['content'] if v['type'] == 'input_image']), 5)
                self.assertLess(len(text_part), 50000)
                oversized = copy.deepcopy(packet)
                oversized['unexpected_text'] = 'x' * 50001
                with self.assertRaisesRegex(ValueError, 'safe input bound'):
                    runtime.review_request(oversized, a.MODEL, 2600, 3)
                keys = list(packet['media'])
                decision = {'status': 'PASS', 'issues': [], 'selection': {job['id']: keys},
                            'source_checks': [{'url': s['url'], 'accessed': True,
                                               'verified_scene_indices': [0, 1, 2, 3], 'independence_group': str(i)}
                                              for i, s in enumerate(job['sources'])]}
                response = {'status': 'completed', 'output': [
                    {'type': 'web_search_call', 'status': 'completed', 'action': {'type': 'open_page', 'url': s['url']}}
                    for s in job['sources']] + [{'content': [{'type': 'output_text', 'text': json.dumps(decision)}]}]}
                reviewed = runtime.apply_review(packet, response)
                runtime.prepare_media(reviewed)
                self.assertEqual([s['media'] for s in reviewed['job']['scenes']], keys)
                self.assertEqual(a.narration(reviewed['job']), a.narration(job))
                damaged = copy.deepcopy(packet); damaged['media'][keys[0]]['credit'] = 'Unapproved'
                with self.assertRaisesRegex(ValueError, 'metadata changed'): runtime.validate_media(damaged)

if __name__ == '__main__': unittest.main()
