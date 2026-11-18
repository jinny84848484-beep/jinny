"""Owner order 2026-11-19: a work order issued the day before is followed first.

The directive only changes which topics are researched first; every fact,
source and readiness rule still applies, and a bad directive never holds a day.
"""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

import cloud_runner as c
import owner_directive as d

DAY = '2026-11-19'


def directive(**extra):
    value = {'day': DAY, 'issued_by': 'CEO', 'issued_at': '2026-11-19T00:10:00+09:00', 'topics': [
        {'title': '추경호 대구시장 1심 무죄', 'angle': '1심은 확정이 아니다', 'hook': '구형은 징역 20년, 선고는 무죄',
         'facts': ['특검 구형 징역 20년'], 'sources': ['https://www.fnnews.com/news/202611181452418552'],
         'avoid': ['재판장 과거 판결 언급']},
        {'title': '가계빚 2,030조', 'facts': ['3분기 말 2030조5000억원'], 'sources': ['https://www.newspim.com/news/view/20261118000562']}]}
    value.update(extra)
    return value


class Load(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(d.p, 'ROOT', Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, value, day=DAY):
        path = Path(self.tmp.name) / 'directives' / 'ojirap' / (day + '.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding='utf-8')
        return path

    def quiet_load(self, day=DAY):
        with redirect_stderr(io.StringIO()) as err:
            result = d.load(day)
        return result, err.getvalue()

    def test_no_directive_changes_nothing(self):
        self.assertEqual(d.load(DAY), (None, None))
        self.assertEqual(d.research_clause(None), '')
        self.assertEqual(d.draft_clause(None), '')

    def test_valid_directive_is_loaded_for_its_day_only(self):
        self.write(directive())
        (value, sha), _ = self.quiet_load()
        self.assertEqual([t['title'] for t in value['topics']], ['추경호 대구시장 1심 무죄', '가계빚 2,030조'])
        self.assertEqual(len(sha), 64)
        self.assertEqual(d.load('2026-11-20'), (None, None))

    def test_bad_directives_are_ignored_not_fatal(self):
        cases = {
            'wrong day': directive(day='2026-11-18'),
            'three topics': directive(topics=directive()['topics'] + directive()['topics'][:1]),
            'http source': directive(topics=[{'title': 'x', 'sources': ['http://example.org/a']}]),
            'empty title': directive(topics=[{'title': ' '}]),
            'not json': '{broken',
        }
        for name, value in cases.items():
            with self.subTest(name=name):
                self.write(value)
                result, err = self.quiet_load()
                self.assertEqual(result, (None, None))
                self.assertIn('Owner directive ignored', err)

    def test_symlink_is_ignored(self):
        real = self.write(directive(), day='2026-11-18')
        link = Path(self.tmp.name) / 'directives' / 'ojirap' / (DAY + '.json')
        link.symlink_to(real)
        self.assertEqual(d.load(DAY), (None, None))

    def test_invalid_day_name_is_rejected(self):
        with self.assertRaises(ValueError):
            d.path_for('../../etc/passwd')


class Prompts(unittest.TestCase):
    def test_research_clause_puts_directive_first_without_relaxing_rules(self):
        text = d.research_clause(d.validate(directive(), DAY))
        self.assertIn('follow it FIRST', text)
        self.assertIn('not verified evidence', text)
        self.assertIn('Never relax a source, readiness', text)
        self.assertIn('replace only that slot', text)
        self.assertIn('추경호 대구시장 1심 무죄', text)

    def test_draft_clause_carries_hook_and_avoid_but_no_new_facts(self):
        text = d.draft_clause(d.validate(directive(), DAY))
        self.assertIn('구형은 징역 20년, 선고는 무죄', text)
        self.assertIn('재판장 과거 판결 언급', text)
        self.assertIn('Facts still come ONLY from the research manifest', text)
        self.assertNotIn('2030조5000억원', text)

    def test_runner_wires_directive_into_research_and_draft(self):
        source = Path(c.__file__).read_text(encoding='utf-8')
        self.assertIn('owner_directive.load(day)', source)
        self.assertIn('owner_directive.research_clause(directive)', source)
        self.assertIn('owner_directive.draft_clause(directive)', source)
        self.assertIn("record['owner_directive']", source)
        # The research rules themselves are unchanged by a directive.
        self.assertNotIn('directive', c.RESEARCH_PROMPT.lower())


if __name__ == '__main__':
    unittest.main()
