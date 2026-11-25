"""CEO order 2026-11-25: 방구석 세계요리 runs end to end without a human step.

Offline only: every provider, YouTube and GitHub call is replaced by a fake.
"""
import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from world_kitchen import daily as a
from world_kitchen import episode as e

DAY = '2026-11-26'
NOW = datetime(2026, 11, 26, 10, 7, tzinfo=a.KST)


def plan():
    return {
        'dish': '퀘사디아', 'country': '멕시코', 'title': '전자레인지 2분, 치즈 퀘사디아',
        'description': '또띠아와 치즈만 있으면 됩니다.\nAI로 만든 이미지와 음성을 사용했습니다.',
        'servings': 1, 'appliance': 'microwave',
        'ingredients': [{'name': '또띠아', 'amount': 1, 'unit': '장'}, {'name': '모차렐라 치즈', 'amount': 30, 'unit': 'g', 'substitute': '피자치즈'}],
        'korean_mart_note': '마트 피자치즈로 충분해요.', 'allergens': ['밀', '우유'],
        'steps': [{'text': '또띠아 반쪽에 치즈를 올리고 접어요', 'appliance': 'none', 'seconds': 0},
                  {'text': '접시에 담아 랩을 느슨하게 씌우고 700W 60초 데워요', 'appliance': 'microwave', 'seconds': 60}],
        'heating': {'seconds': {'700': 60, '1000': 40}, 'source': 'https://example.org/recipe-a',
                    'basis': '또띠아 1장, 치즈 30g', 'caveat': '1000W는 700W 시간을 비례 환산, 기기마다 차이'},
        'sources': ['https://example.org/recipe-a', 'https://example.net/recipe-b'],
        'scenes': [
            {'kind': 'hero', 'title': '치즈 퀘사디아', 'body': '전자레인지면 끝', 'footer': '1인분', 'narration': '전자레인지만 있으면 퀘사디아가 됩니다.', 'english': 'All you need is a microwave.', 'image_prompt': 'Finished cheese quesadilla cut in wedges, no text'},
            {'kind': 'ingredients', 'title': '재료', 'body': '또띠아 1장, 치즈 30g', 'footer': '피자치즈도 OK', 'narration': '또띠아 한 장과 치즈 삼십 그램이면 돼요.', 'english': 'One tortilla and thirty grams of cheese.', 'image_prompt': 'Tortilla and shredded cheese on a counter, no text'},
            {'kind': 'step', 'title': '접기', 'body': '치즈 올리고 반 접기', 'footer': '가장자리 1cm 남기기', 'narration': '반쪽에 치즈를 올리고 접어 주세요.', 'english': 'Add cheese to one half and fold.', 'image_prompt': 'Hands folding a tortilla over cheese, no text'},
            {'kind': 'heating', 'title': '데우기', 'body': '700W 60초 · 1000W 40초', 'footer': '랩은 느슨하게', 'narration': '랩을 느슨하게 씌우고 칠백 와트에서 일 분 데워요.', 'english': 'Cover loosely and heat for one minute at 700W.', 'image_prompt': 'Plate with folded tortilla inside a microwave, no text'},
            {'kind': 'finish', 'title': '완성', 'body': '잘라서 바로 먹기', 'footer': '뜨거우니 조심', 'narration': '잘라서 따뜻할 때 드세요.', 'english': 'Cut and enjoy while warm.', 'image_prompt': 'Quesadilla wedges with melted cheese pull, no text'},
        ],
    }


class BuildEpisode(unittest.TestCase):
    def build(self, value):
        with tempfile.TemporaryDirectory() as d:
            return e.build_episode(value, DAY, Path(d))

    def test_valid_plan_meets_renderer_and_safety_guards(self):
        episode = self.build(plan())
        self.assertEqual(episode['scenes'][-1]['narration'], e.SIGNATURE)
        self.assertTrue(episode['description'].endswith('WK-20261126'))
        self.assertEqual(episode['heating']['seconds'], {'700': 60, '1000': 40})

    def test_content_problems_hold_not_alarm(self):
        cases = {
            'foil': lambda v: v['steps'].__setitem__(1, {**v['steps'][1], 'text': '호일에 싸서 700W 60초 전자레인지에 돌려요'}),
            'one source': lambda v: v.__setitem__('sources', ['https://example.org/recipe-a']),
            'no AI notice': lambda v: v.__setitem__('description', '설명만'),
            'time mismatch': lambda v: v['scenes'][3].__setitem__('body', '700W 90초 · 1000W 60초'),
            'too few scenes': lambda v: v.__setitem__('scenes', v['scenes'][:3]),
            'missing field': lambda v: v.pop('title'),
        }
        for name, change in cases.items():
            with self.subTest(name=name):
                value = plan()
                change(value)
                with self.assertRaises(e.Hold):
                    self.build(value)


class Timing(unittest.TestCase):
    def test_durations_fit_renderer_limits(self):
        self.assertEqual(e.fit_durations([2.0, 3.0, 2.5, 3.0, 2.0, 2.5]), [2.35, 3.35, 2.85, 3.35, 2.35, 2.85])
        with self.assertRaises(e.Hold):
            e.fit_durations([1.0, 1.0, 1.0, 1.0])
        with self.assertRaises(e.Hold):
            e.fit_durations([14.0] * 6)

    def test_publish_at_19_or_soon_after_a_late_run(self):
        self.assertEqual(e.publish_time(NOW).hour, 19)
        late = datetime(2026, 11, 26, 18, 50, tzinfo=a.KST)
        self.assertEqual(e.publish_time(late), datetime(2026, 11, 26, 19, 10, tzinfo=a.KST))


class Run(unittest.TestCase):
    def setUp(self):
        self.store = {}
        self.writes = []
        for target, value in (('read_ledger', lambda: (copy.deepcopy(self.store), 'sha')),
                              ('write_ledger', self.save)):
            p = patch.object(a, target, side_effect=value)
            p.start()
            self.addCleanup(p.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = patch.object(a, 'ROOT', Path(tmp.name))
        p.start()
        self.addCleanup(p.stop)

    def save(self, ledger, sha, day):
        self.store = copy.deepcopy(ledger)
        self.writes.append(ledger[day]['status'])
        return 'sha'

    def quiet(self, **patches):
        with patch.multiple(a, **patches), redirect_stdout(io.StringIO()) as out:
            return a.run(NOW), out.getvalue()

    def test_scheduled_day_does_nothing(self):
        self.store = {DAY: {'status': 'scheduled', 'video_id': 'x'}}
        result, _ = self.quiet(preflight=lambda: self.fail('no provider call'))
        self.assertEqual(result, 'done')

    def test_held_twice_stops_for_the_day(self):
        self.store = {DAY: {'status': 'held', 'attempts': 2, 'reason': 'r'}}
        result, _ = self.quiet(preflight=lambda: self.fail('no provider call'))
        self.assertEqual(result, 'held')

    def test_existing_upload_is_recorded_not_repeated(self):
        result, _ = self.quiet(preflight=lambda: ('t', 'UU'), find_existing=lambda *x: 'vid1')
        self.assertEqual(result, 'done')
        self.assertEqual(self.store[DAY]['status'], 'found_existing')

    def test_interrupted_upload_never_retries_blindly(self):
        self.store = {DAY: {'status': 'upload_submitted', 'attempts': 1}}
        with self.assertRaisesRegex(RuntimeError, 'Interrupted upload'):
            self.quiet(preflight=lambda: ('t', 'UU'), find_existing=lambda *x: None)
        self.assertEqual(self.writes, [])

    def test_missing_oauth_is_a_ceo_alarm(self):
        with patch.dict(os.environ, {k: 'x' for k in a.REQUIRED_ENV if k != 'YOUTUBE_KITCHEN_OAUTH'}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'CEO ALARM.*YOUTUBE_KITCHEN_OAUTH'):
                a.preflight()

    def test_review_fail_holds_with_warning(self):
        answers = iter([json.dumps(plan(), ensure_ascii=False), json.dumps({'status': 'FAIL', 'issues': [{'code': 'heating_time', 'explanation': 'x'}]})])
        result, out = self.quiet(preflight=lambda: ('t', 'UU'), find_existing=lambda *x: None,
                                 openai_text=lambda *x, **k: next(answers), openai_image=lambda *x: self.fail('no image before review'))
        self.assertEqual(result, 'held')
        self.assertIn('::warning', out)
        self.assertEqual(self.store[DAY]['attempts'], 1)

    def test_full_day_is_scheduled_once(self):
        answers = iter([json.dumps(plan(), ensure_ascii=False), json.dumps({'status': 'PASS', 'issues': []})])
        images, uploads = [], []

        def narrate(episode, folder):
            for scene in episode['scenes']:
                scene['duration'] = 3
            episode['narration_audio'] = str(folder / 'n.m4a')

        def render(episode, output):
            output.mkdir(parents=True)
            video = output / 'video.mp4'
            video.write_bytes(b'video')
            return video

        result, out = self.quiet(preflight=lambda: ('t', 'UU'), find_existing=lambda *x: None,
                                 openai_text=lambda *x, **k: next(answers), openai_image=lambda prompt, path: images.append(prompt),
                                 narrate=narrate, render=render, make_logo=lambda path: path,
                                 upload=lambda video, ep, token, at: uploads.append(at) or 'vid9')
        self.assertEqual(result, 'scheduled')
        self.assertEqual(len(images), 5)  # signature card reuses the hero photo
        self.assertTrue(all('no text' in p for p in images))
        self.assertEqual(uploads[0].hour, 19)
        self.assertEqual(self.writes, ['planning', 'upload_submitted', 'scheduled'])
        self.assertEqual(self.store[DAY]['dish'], '퀘사디아')

class OAuthShape(unittest.TestCase):
    GOOD = {'channel_id': 'UCkuz6ra5_hZ1MSAbFthqDcg', 'client_id': '1067393655743-b4aos8sufhvn89ipe1juokprjvlv9sq3.apps.googleusercontent.com',
            'client_secret': 'GOCSPX-abc', 'refresh_token': '1//abc'}

    def token(self, value):
        from world_kitchen import providers as pr
        calls = []

        def fake_http(url, data=None, headers=None, method=None, timeout=180):
            calls.append(data)
            if 'oauth2' in url:
                return {'access_token': 't'}
            return {'items': [{'id': 'UCkuz6ra5_hZ1MSAbFthqDcg', 'contentDetails': {'relatedPlaylists': {'uploads': 'UU1'}}}]}
        with patch.dict(os.environ, {'YOUTUBE_KITCHEN_OAUTH': json.dumps(value)}), patch.object(pr, 'http', side_effect=fake_http):
            return pr.youtube_token(), calls

    def test_spaces_are_trimmed(self):
        (token, uploads), calls = self.token({k: ' ' + v + ' ' for k, v in self.GOOD.items()})
        self.assertEqual(uploads, 'UU1')
        self.assertIn(b'client_id=1067393655743-b4aos8sufhvn89ipe1juokprjvlv9sq3.apps.googleusercontent.com&', calls[0])

    def test_wrong_shapes_name_the_problem_without_secrets(self):
        cases = {'<클라이언트 ID>': 'client_id looks wrong', 'abc.apps.googleusercontent.com': 'client_id looks wrong'}
        for client_id, message in cases.items():
            with self.subTest(client_id=client_id):
                with self.assertRaisesRegex(RuntimeError, 'CEO ALARM.*' + message) as ctx:
                    self.token({**self.GOOD, 'client_id': client_id})
                self.assertNotIn('GOCSPX-abc', str(ctx.exception))
        with self.assertRaisesRegex(RuntimeError, 'missing refresh_token'):
            self.token({k: v for k, v in self.GOOD.items() if k != 'refresh_token'})
        with self.assertRaisesRegex(RuntimeError, 'GOCSPX-'):
            self.token({**self.GOOD, 'client_secret': '여기에_비밀번호'})


if __name__ == '__main__':
    unittest.main()
