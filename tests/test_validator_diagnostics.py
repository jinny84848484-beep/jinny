import ast
import inspect
import json
import unittest
from unittest.mock import patch
import cloud_runner as c
from test_guards import batch


class ValidatorDiagnostics(unittest.TestCase):
    def test_every_fixed_validation_error_has_distinct_reason(self):
        labels={}
        for function in (c.validate_batch,c.p.validate,c.correct_narration_lengths_once):
            for node in ast.walk(ast.parse(inspect.getsource(function))):
                if not isinstance(node,ast.Raise) or not isinstance(node.exc,ast.Call):continue
                if not isinstance(node.exc.func,ast.Name) or node.exc.func.id!='ValueError':continue
                self.assertIsInstance(node.exc.args[0],ast.Constant)
                message=node.exc.args[0].value
                reason=c.draft_validation_diagnostics([],ValueError(message))['reason']
                self.assertNotEqual(reason,'draft_schema_or_validation',message)
                if message not in labels:self.assertNotIn(reason,labels.values())
                labels[message]=reason

    def test_real_source_and_scene_guards_hold_without_correction(self):
        mutations=[
            (lambda j:j[0].update(sources=j[0]['sources'][:1]),'source_domain_diversity'),
            (lambda j:j[0]['scenes'][0].update(source_indices=[]),'fact_citations_missing'),
            (lambda j:j[0]['scenes'][0].update(source_indices=[99]),'citation_index'),
            (lambda j:j[0].update(id='2026-11-05-UPPERCASE'),'job_id_format'),
            (lambda j:j[0].pop('sources'),'missing_sources'),
        ]
        for mutate,reason in mutations:
            jobs=batch();mutate(jobs)
            ledger={'days':{'2026-11-05':{'status':'research_submitted'}},'publications':[{'video_id':'old'}]}
            with self.subTest(reason=reason),patch.object(c,'journal_put'),patch.object(c,'correct_narration_lengths_once') as correction,patch.object(c,'http') as network:
                with self.assertRaisesRegex(RuntimeError,reason):
                    c.guarded_draft_validation(ledger,'sha','2026-11-05',json.dumps({'jobs':jobs}),set(),allow_length_correction=True)
                correction.assert_not_called();network.assert_not_called()
                self.assertTrue(ledger['days']['2026-11-05']['retry_hold'])
                self.assertEqual(ledger['publications'],[{'video_id':'old'}])

    def test_arbitrary_exception_text_never_leaks(self):
        secret='private title https://secret.invalid credential'
        for error in [ValueError(secret),KeyError(secret),TypeError(secret),IndexError(secret)]:
            result=c.draft_validation_diagnostics([],error)
            self.assertNotIn(secret,json.dumps(result))
        self.assertEqual(c.draft_validation_diagnostics([],KeyError('sources'))['reason'],'missing_sources')

    def test_unknown_error_does_not_impersonate_schema_key(self):
        self.assertEqual(c.draft_validation_diagnostics([],KeyError(('sources',)))['reason'],'missing_schema_field')
        self.assertEqual(c.draft_validation_diagnostics([],ValueError('sources'))['reason'],'draft_schema_or_validation')
