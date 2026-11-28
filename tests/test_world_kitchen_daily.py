"""CEO order 2026-11-25: 방구석 세계요리 runs end to end without a human step.

Offline only: every provider, YouTube and GitHub call is replaced by a fake.
"""
import copy
import io
import json
import os
import secrets
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
                    'basis': '또띠아 1장, 치즈 30g', 'caveat': '1000W는 700W 시간을 비례 환산, 기기마다 차이',
                    'source_watts': 700, 'source_intervals': [{'seconds': 60, 'after': 'stop'}]},
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

    def test_overnight_run_does_not_open_an_attempt(self):
        with patch.object(a, 'preflight', return_value=('t', 'UU')), \
             patch.object(a, 'find_existing', return_value=None) as existing, \
             patch.object(a, 'openai_text') as planner, redirect_stdout(io.StringIO()):
            for hour, minute in ((0, 0), (1, 47), (8, 59)):
                self.assertEqual(a.run(NOW.replace(hour=hour, minute=minute)), 'before_window')
        self.assertEqual(existing.call_count, 3)
        planner.assert_not_called()
        self.assertEqual(self.writes, [])
        self.assertEqual(self.store, {})

    def test_overnight_existing_upload_is_reconciled(self):
        with patch.object(a, 'preflight', return_value=('t', 'UU')), \
             patch.object(a, 'find_existing', return_value='vid1'), \
             patch.object(a, 'openai_text') as planner, redirect_stdout(io.StringIO()):
            self.assertEqual(a.run(NOW.replace(hour=1, minute=47)), 'done')
        planner.assert_not_called()
        self.assertEqual(self.writes, ['found_existing'])
        self.assertEqual(self.store[DAY]['video_id'], 'vid1')

    def test_overnight_interrupted_submission_is_not_retried(self):
        self.store = {DAY: {'status': 'upload_submitted', 'attempts': 1}}
        with patch.object(a, 'preflight', return_value=('t', 'UU')), \
             patch.object(a, 'find_existing', return_value=None), \
             patch.object(a, 'openai_text') as planner:
            with self.assertRaisesRegex(RuntimeError, 'Interrupted upload'):
                a.run(NOW.replace(hour=1, minute=47))
        planner.assert_not_called()
        self.assertEqual(self.writes, [])

    def test_production_window_opens_at_nine_kst(self):
        with patch.object(a, 'preflight', return_value=('t', 'UU')), \
             patch.object(a, 'find_existing', return_value=None), \
             patch.object(a, 'openai_text', side_effect=a.Hold('test-only stop')) as planner, \
             redirect_stdout(io.StringIO()):
            self.assertEqual(a.run(NOW.replace(hour=9, minute=0)), 'held')
        planner.assert_called_once()
        self.assertEqual(self.writes, ['planning', 'held'])
        self.assertEqual(self.store[DAY]['attempts'], 1)

    def test_interrupted_upload_never_retries_blindly(self):
        self.store = {DAY: {'status': 'upload_submitted', 'attempts': 1}}
        with self.assertRaisesRegex(RuntimeError, 'Interrupted upload'):
            self.quiet(preflight=lambda: ('t', 'UU'), find_existing=lambda *x: None)
        self.assertEqual(self.writes, [])

    def test_missing_oauth_is_a_ceo_alarm(self):
        with patch.dict(os.environ, {k: 'x' for k in a.REQUIRED_ENV if k != 'YOUTUBE_KITCHEN_OAUTH'}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'CEO ALARM.*YOUTUBE_KITCHEN_OAUTH'):
                a.preflight()

    FAIL = json.dumps({'status': 'FAIL', 'issues': [{'code': 'heating_time', 'explanation': 'x'}]})

    def test_review_fail_twice_holds_with_warning(self):
        prompts = []
        answers = iter([json.dumps(plan(), ensure_ascii=False), self.FAIL, json.dumps(plan(), ensure_ascii=False), self.FAIL])

        def text(prompt, instructions, **k):
            prompts.append(instructions)
            return next(answers)
        result, out = self.quiet(preflight=lambda: ('t', 'UU'), find_existing=lambda *x: None,
                                 openai_text=text, openai_image=lambda *x: self.fail('no image before review'))
        self.assertEqual(result, 'held')
        self.assertIn('::warning', out)
        self.assertEqual(self.store[DAY]['attempts'], 1)
        self.assertIn('heating_time', self.store[DAY]['reason'])
        # exactly one revision, then one fresh review; never a second revision
        self.assertEqual(prompts, [e.PLAN_PROMPT, e.REVIEW_PROMPT, e.REVISE_PROMPT, e.REVIEW_PROMPT])

    def test_revision_that_passes_review_continues(self):
        revised = plan()
        revised['title'] = '전자레인지 1분, 치즈 퀘사디아'
        answers = iter([json.dumps(plan(), ensure_ascii=False), self.FAIL, json.dumps(revised, ensure_ascii=False),
                        json.dumps({'status': 'PASS', 'issues': []})])
        def stop(prompt, path):
            raise RuntimeError('images started')
        # Reaching the image step proves the revised plan passed its fresh review.
        with self.assertRaisesRegex(RuntimeError, 'images started'):
            self.quiet(preflight=lambda: ('t', 'UU'), find_existing=lambda *x: None,
                       openai_text=lambda *x, **k: next(answers), openai_image=stop)
        self.assertEqual(self.store[DAY]['status'], 'planning')

    def test_initial_amount_guard_uses_single_repair_then_fresh_review(self):
        first = plan()
        first['ingredients'].append({'name': '우유', 'unit': '밥숟가락', 'amount': 37})
        replacement = {**plan(), 'dish': '새 요리'}
        answers = iter([json.dumps(first), json.dumps(replacement), json.dumps({'status': 'PASS', 'issues': []})])
        calls = []

        def text(prompt, instructions, **kwargs):
            calls.append((prompt, instructions))
            return next(answers)
        def stop(*args):
            raise RuntimeError('images started')
        with self.assertRaisesRegex(RuntimeError, 'images started'):
            self.quiet(preflight=lambda: ('t', 'UU'), find_existing=lambda *x: None,
                       openai_text=text, openai_image=stop)
        self.assertEqual([x[1] for x in calls], [e.PLAN_PROMPT, e.REVISE_PROMPT, e.REVIEW_PROMPT])
        self.assertIn('kitchen_amount', calls[1][0])
        self.assertIn('Recent dishes to avoid', calls[1][0])
        self.assertIn(json.dumps([first['dish']], ensure_ascii=False), calls[1][0])

    def test_source_three_intervals_cannot_collapse_to_one_continuous_heat(self):
        bad = plan()
        bad['heating']['source_intervals'] = [
            {'seconds': 60, 'after': 'stir'}, {'seconds': 30, 'after': 'stir'},
            {'seconds': 10, 'after': 'stop'},
        ]
        bad['heating']['seconds'] = {'700': 100, '1000': 70}
        bad['steps'][1].update(text='700W 100초 연속으로 돌려요.', seconds=100)
        bad['scenes'][3]['body'] = '700W 100초 · 1000W 70초'
        answers = iter([json.dumps(bad), json.dumps(bad), self.FAIL, self.FAIL])
        calls = []
        def text(prompt, instructions, **kwargs):
            calls.append(instructions)
            return next(answers)
        result, _ = self.quiet(preflight=lambda: ('t', 'UU'), find_existing=lambda *x: None,
                               openai_text=text, openai_image=lambda *x: self.fail('no image'),
                               upload=lambda *x: self.fail('no upload'))
        self.assertEqual(result, 'held')
        self.assertEqual(calls, [e.PLAN_PROMPT, e.REVISE_PROMPT])
        self.assertEqual(self.store[DAY]['reason'], 'guard: kitchen_heating_sequence')

    def test_same_interval_total_still_requires_stirring_at_each_boundary(self):
        value = plan()
        value['heating']['source_intervals'] = [
            {'seconds': 30, 'after': 'stir'}, {'seconds': 30, 'after': 'stop'},
        ]
        step = {'text': '700W 30초 돌려요.', 'seconds': 30, 'appliance': 'microwave'}
        value['steps'] = [step, copy.deepcopy(step)]
        with self.assertRaisesRegex(e.Hold, 'kitchen_heating_sequence'):
            e.verify_source_heat(value)
        value['steps'][0] = {**step, 'text': '700W 30초 돌린 뒤 저어요.'}
        e.verify_source_heat(value)
        value['heating']['source_watts'] = None
        with self.assertRaisesRegex(e.Hold, 'kitchen_heating_source'):
            e.verify_source_heat(value)

    def test_revised_amount_guard_does_not_allow_extra_repair_or_upload(self):
        bad = plan()
        bad['ingredients'].append({'name': '우유', 'unit': '밥숟가락', 'amount': 37})
        answers = iter([json.dumps(plan()), self.FAIL, json.dumps(bad)])
        result, _ = self.quiet(preflight=lambda: ('t', 'UU'), find_existing=lambda *x: None,
                               openai_text=lambda *x, **k: next(answers),
                               openai_image=lambda *x: self.fail('no images'),
                               upload=lambda *x: self.fail('no upload'))
        self.assertEqual(result, 'held')
        self.assertEqual(self.store[DAY]['reason'], 'guard: kitchen_amount')

    def test_revision_cannot_repeat_a_recent_dish(self):
        self.store['2026-11-20'] = {'status': 'scheduled', 'dish': '김치전'}
        revised = plan()
        revised['dish'] = '김치전'
        answers = iter([json.dumps(plan(), ensure_ascii=False), self.FAIL, json.dumps(revised, ensure_ascii=False)])
        result, out = self.quiet(preflight=lambda: ('t', 'UU'), find_existing=lambda *x: None,
                                 openai_text=lambda *x, **k: next(answers), openai_image=lambda *x: self.fail('no image'))
        self.assertEqual(result, 'held')
        self.assertIn('recent', self.store[DAY]['reason'])

    def test_next_attempt_does_not_repeat_rejected_dish(self):
        self.store[DAY] = {'status': 'held', 'attempts': 1, 'rejected_dish': plan()['dish']}
        answers = iter([json.dumps(plan()), json.dumps(plan())])
        calls = []
        def text(prompt, instructions, **kwargs):
            calls.append((prompt, instructions))
            return next(answers)
        result, _ = self.quiet(preflight=lambda: ('t', 'UU'), find_existing=lambda *x: None,
                               openai_text=text, openai_image=lambda *x: self.fail('no images'))
        self.assertEqual(result, 'held')
        self.assertEqual(self.store[DAY]['attempts'], 2)
        self.assertIn(json.dumps([plan()['dish']], ensure_ascii=False), calls[0][0])
        self.assertEqual([call[1] for call in calls], [e.PLAN_PROMPT, e.REVISE_PROMPT])

    def test_backup_excludes_both_dishes_rejected_in_the_first_run(self):
        first = plan()
        first['ingredients'].append({'name': '우유', 'unit': '밥숟가락', 'amount': 37})
        second = copy.deepcopy(first)
        second['dish'] = '다른 테스트 요리'
        responses = iter(map(json.dumps, (first, second, first, second)))
        calls = []
        def text(prompt, instructions, **kwargs):
            calls.append((prompt, instructions))
            return next(responses)
        fakes = dict(preflight=lambda: ('t', 'UU'), find_existing=lambda *x: None,
                     openai_text=text, openai_image=lambda *x: self.fail('no media before review'),
                     upload=lambda *x: self.fail('never upload a held recipe'))
        self.assertEqual(self.quiet(**fakes)[0], 'held')
        self.assertEqual(self.quiet(**fakes)[0], 'held')
        self.assertEqual(self.quiet(**fakes)[0], 'held')
        self.assertEqual(len(calls), 4)
        expected = json.dumps([first['dish'], second['dish']], ensure_ascii=False)
        self.assertIn(expected, calls[2][0])
        self.assertIn(expected, calls[3][0])
        self.assertEqual(self.store[DAY]['attempts'], 2)
        self.assertEqual(self.writes, ['planning', 'held', 'planning', 'held'])

    def test_planner_checklist_covers_review_failures(self):
        for words in ('brand or manufacturer site', 'Attribution', 'No brand names', 'Measurements', 'heating.basis', 'natural spoken Korean'):
            self.assertIn(words, e.PLAN_PROMPT)
            self.assertIn(words, e.REVISE_PROMPT)
        self.assertIn('untrusted evidence', e.REVISE_PROMPT)
        # CEO order 2026-11-26: source first, exact quantity, origin as the page states it
        for words in ('SOURCE FIRST', 'never pick the dish first', 'no scaling', 'origin exactly as a cited page states'):
            self.assertIn(words, e.PLAN_PROMPT)
        self.assertIn('no real brand names', e.REVIEW_PROMPT)  # the safety review keeps the brand rule

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

        # The runner schedules from render completion, not the run's injected start time.
        with patch.object(a, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = NOW
            result, out = self.quiet(preflight=lambda: ('t', 'UU'), find_existing=lambda *x: None,
                                     openai_text=lambda *x, **k: next(answers), openai_image=lambda prompt, path: images.append(prompt),
                                     narrate=narrate, render=render, make_logo=lambda path: path,
                                     upload=lambda video, ep, token, at: uploads.append(at) or 'vid9')
        self.assertEqual(result, 'scheduled')
        self.assertEqual(len(images), 5)  # signature card reuses the hero photo
        self.assertTrue(all('no text' in p for p in images))
        self.assertEqual(uploads, [datetime(2026, 11, 26, 19, 0, tzinfo=a.KST)])
        self.assertEqual(self.writes, ['planning', 'upload_submitted', 'scheduled'])
        self.assertEqual(self.store[DAY]['dish'], '퀘사디아')

class OAuthShape(unittest.TestCase):
    GOOD = {'channel_id': 'UCkuz6ra5_hZ1MSAbFthqDcg', 'client_id': '123456789000-testfixture.apps.googleusercontent.com',
            'client_secret': 'GOCSPX-' + secrets.token_hex(12), 'refresh_token': '1//' + secrets.token_hex(24)}

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
        self.assertIn(b'client_id=123456789000-testfixture.apps.googleusercontent.com&', calls[0])

    def test_wrong_shapes_name_the_problem_without_secrets(self):
        cases = {'<클라이언트 ID>': 'client_id looks wrong', 'abc.apps.googleusercontent.com': 'client_id looks wrong'}
        for client_id, message in cases.items():
            with self.subTest(client_id=client_id):
                with self.assertRaisesRegex(RuntimeError, 'CEO ALARM.*' + message) as ctx:
                    self.token({**self.GOOD, 'client_id': client_id})
                self.assertNotIn(self.GOOD['client_secret'], str(ctx.exception))
                self.assertNotIn(self.GOOD['refresh_token'], str(ctx.exception))
        with self.assertRaisesRegex(RuntimeError, 'missing refresh_token'):
            self.token({k: v for k, v in self.GOOD.items() if k != 'refresh_token'})
        with self.assertRaisesRegex(RuntimeError, 'GOCSPX-'):
            self.token({**self.GOOD, 'client_secret': '여기에_비밀번호'})


if __name__ == '__main__':
    unittest.main()
