"""CEO order 2026-11-25: Instagram card news uploads automatically.

Offline only: no Instagram or GitHub call is made; the client and the record
store are replaced with fakes.
"""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import instagram_cards as ig

DAY = '2026-11-27'
NOW = datetime(2026, 11, 27, 13, 5, tzinfo=ig.KST)


def jpeg(w, h):
    # Minimal JPEG header with a baseline SOF0 segment carrying the size.
    sof = b'\xff\xc0\x00\x11\x08' + h.to_bytes(2, 'big') + w.to_bytes(2, 'big') + b'\x03' + b'\x00' * 9
    return b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00' + sof + b'\xff\xd9'


def issue(**extra):
    value = {'day': DAY, 'self_check': 'pass', 'caption': '이번 주말 갈 곳 #주말 \n\n출처: 각 공식 페이지',
             'sources': ['https://a.example/1', 'https://b.example/2'],
             'cards': [{'file': '01.jpg', 'alt': '표지'}, {'file': '02.jpg'}, {'file': '03.jpg'}]}
    value.update(extra)
    return value


class Fake:
    def __init__(self, existing=None, quota=50):
        self.existing, self.quota, self.published = existing, quota, None

    def check_account(self):
        pass

    def already_posted(self, code):
        return self.existing

    def quota_left(self):
        return self.quota

    def publish(self, issue, base):
        self.published = (issue, base)
        return {'media_id': '1', 'permalink': 'https://www.instagram.com/p/x/'}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name, value in (('ISSUES', self.root),):
            p = patch.object(ig, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.store = {}
        p = patch.object(ig, 'read_posted', lambda: (dict(self.store), None))
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(ig, 'write_posted', lambda day, entry: self.store.__setitem__(day, entry))
        p.start()
        self.addCleanup(p.stop)
        p = patch.dict(os.environ, {'IG_TOKEN_ISSUED_ON': '2026-11-20', 'GITHUB_REPOSITORY': 'o/r', 'GITHUB_SHA': 'abc'})
        p.start()
        self.addCleanup(p.stop)

    def write(self, value=None, review=None, sizes=((1080, 1350),) * 3, day=DAY):
        folder = self.root / day
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'issue.json').write_text(json.dumps(value or issue(), ensure_ascii=False), encoding='utf-8')
        if review is not None:
            (folder / 'review.json').write_text(json.dumps(review), encoding='utf-8')
        for i, (w, h) in enumerate(sizes, 1):
            (folder / ('%02d.jpg' % i)).write_bytes(jpeg(w, h))
        return folder

    def quiet(self, **kw):
        with redirect_stdout(io.StringIO()) as out:
            result = ig.run(**kw)
        return result, out.getvalue()


class Validation(Base):
    def test_good_issue_is_published_once_with_code(self):
        self.write()
        fake = Fake()
        result, _ = self.quiet(now=NOW, client=fake)
        self.assertEqual(result, 'posted')
        posted, base = fake.published
        self.assertTrue(posted['caption'].endswith('WM-20261127'))
        self.assertEqual(base, 'https://raw.githubusercontent.com/o/r/abc/instagram/issues/2026-11-27/')
        self.assertEqual(self.store[DAY]['code'], 'WM-20261127')
        again = Fake()
        self.assertEqual(self.quiet(now=NOW, client=again)[0], 'done')
        self.assertIsNone(again.published)

    def test_post_already_on_instagram_is_recorded_not_reposted(self):
        self.write()
        fake = Fake(existing={'id': '9', 'permalink': 'p'})
        self.assertEqual(self.quiet(now=NOW, client=fake)[0], 'done')
        self.assertIsNone(fake.published)
        self.assertEqual(self.store[DAY]['recorded'], 'found_existing')

    def test_holds_never_publish(self):
        cases = {
            'self check missing': (issue(self_check='todo'), None, ((1080, 1350),) * 3),
            'one source': (issue(sources=['https://a.example']), None, ((1080, 1350),) * 3),
            'no 출처 in caption': (issue(caption='주말'), None, ((1080, 1350),) * 3),
            'review fail': (issue(), {'verdict': 'FAIL', 'note': '3번 날짜 오류'}, ((1080, 1350),) * 3),
            'too tall': (issue(), None, ((1080, 1920),) * 3),
            'mixed shapes': (issue(), None, ((1080, 1350), (1080, 1080), (1080, 1350))),
            'too many tags': (issue(caption='출처 ' + ' '.join('#t%d' % i for i in range(31))), None, ((1080, 1350),) * 3),
        }
        for name, (value, review, sizes) in cases.items():
            with self.subTest(name=name):
                folder = self.write(value, review, sizes)
                fake = Fake()
                self.assertEqual(self.quiet(now=NOW, client=fake)[0], 'hold')
                self.assertIsNone(fake.published)
                for f in folder.iterdir():
                    f.unlink()

    def test_review_exclusion_drops_card(self):
        self.write(review={'verdict': 'PASS', 'exclude': ['02.jpg']})
        fake = Fake()
        self.quiet(now=NOW, client=fake)
        self.assertEqual([c['file'] for c in fake.published[0]['cards']], ['01.jpg', '03.jpg'])

    def test_carousel_needs_two_cards_after_exclusion(self):
        self.write(review={'verdict': 'PASS', 'exclude': ['02.jpg', '03.jpg']})
        self.assertEqual(self.quiet(now=NOW, client=Fake())[0], 'hold')

    def test_mpo_is_rejected(self):
        with self.assertRaises(ig.Hold):
            ig.jpeg_size(b'\xff\xd8\xff\xe1\x00\x10MPF\x00' + b'\x00' * 20)


class Timing(Base):
    def test_not_before_1230_kst(self):
        self.write()
        fake = Fake()
        self.assertEqual(self.quiet(now=datetime(2026, 11, 27, 12, 0, tzinfo=ig.KST), client=fake)[0], 'idle')
        self.assertIsNone(fake.published)

    def test_late_run_next_morning_still_posts_but_not_after(self):
        self.write()
        self.assertEqual(self.quiet(now=datetime(2026, 11, 28, 9, 0, tzinfo=ig.KST), client=Fake())[0], 'posted')
        self.store.clear()
        fake = Fake()
        self.assertEqual(self.quiet(now=datetime(2026, 11, 28, 13, 0, tzinfo=ig.KST), client=fake)[0], 'idle')
        self.assertIsNone(fake.published)

    def test_quota_used_up_holds(self):
        self.write()
        self.assertEqual(self.quiet(now=NOW, client=Fake(quota=0))[0], 'hold')


class Connection(unittest.TestCase):
    def test_token_age_alarm(self):
        self.assertEqual(ig.token_age_check('2026-11-01', date(2026, 11, 27)), 26)
        with self.assertRaisesRegex(RuntimeError, 'CEO ALARM'):
            ig.token_age_check('2026-10-01', date(2026, 11, 27))
        with self.assertRaisesRegex(RuntimeError, 'CEO ALARM'):
            ig.token_age_check('', date(2026, 11, 27))

    def test_wrong_account_is_alarm(self):
        client = ig.Instagram('t', '42', sleep=lambda s: None)
        with patch.object(client, 'get', return_value={'username': 'someone', 'user_id': '42'}):
            with self.assertRaisesRegex(RuntimeError, 'CEO ALARM'):
                client.check_account()

    def test_publish_sequence(self):
        client = ig.Instagram('t', '42', sleep=lambda s: None)
        calls = []

        def post(path, **params):
            calls.append((path, params))
            return {'id': 'c%d' % len(calls)}
        with patch.object(client, 'post', side_effect=post), \
                patch.object(client, 'get', side_effect=lambda path, **p: {'status_code': 'FINISHED', 'permalink': 'L'}):
            result = client.publish({'cards': [{'file': '01.jpg', 'alt': 'a'}, {'file': '02.jpg', 'alt': ''}],
                                     'caption': 'x WM-1', 'ai_generated': True}, 'https://h/')
        self.assertEqual(calls[0], ('/42/media', {'image_url': 'https://h/01.jpg', 'is_carousel_item': 'true', 'alt_text': 'a'}))
        self.assertNotIn('alt_text', calls[1][1])
        self.assertEqual(calls[2][1]['media_type'], 'CAROUSEL')
        self.assertEqual(calls[2][1]['children'], 'c1,c2')
        self.assertEqual(calls[2][1]['is_ai_generated'], 'true')
        self.assertNotIn('is_ai_generated', calls[0][1])  # Meta: only on the carousel container
        self.assertEqual(calls[3], ('/42/media_publish', {'creation_id': 'c3'}))
        self.assertEqual(result, {'media_id': 'c4', 'permalink': 'L'})

    def test_container_error_stops(self):
        client = ig.Instagram('t', '42', sleep=lambda s: None)
        with patch.object(client, 'get', return_value={'status_code': 'ERROR'}):
            with self.assertRaises(RuntimeError):
                client.wait('c1')


if __name__ == '__main__':
    unittest.main()
