import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('ask_tools', Path(__file__).parents[1] / 'scripts/16_counter_tools.py')
tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tools)


def arguments(**overrides):
    values = dict(counter='AirMac', ne_type='ADPF', question='What does it measure?',
                  rows=5, family=None, data_version=None, schema_id=None, location=None)
    values.update(overrides)
    return argparse.Namespace(**values)


ITEM = dict(counter_label='AirMac(count)', family='Air', version='1', unit_label='count',
            schema_id='a' * 64, counter_ordinal=1)
ROW = dict(raw_value='14', ne_id='ADPF_300', system_id='1', ne_name='Private', location='',
           init_time_utc='2026-01-01 00:00:00', gran_period_raw='15')


class CounterAskTests(unittest.TestCase):
    def test_cli_routes_ask_without_a_search_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'ran-ai'
            config = root / 'config/clickhouse-server'
            config.mkdir(parents=True)
            (config / 'credentials.json').write_text('{}')
            with patch.object(tools.Path, 'home', return_value=Path(temporary)), \
                 patch('sys.argv', ['16_counter_tools.py', 'ask', '--counter', 'AirMac', '--ne-type', 'uADPF', 'What is its unit?']), \
                 patch.object(tools, 'ask_counter', return_value=0) as ask:
                self.assertEqual(tools.main(), 0)
                self.assertEqual(ask.call_args.args[0].question, 'What is its unit?')
                self.assertEqual(ask.call_args.args[0].ne_type, 'uADPF')
                self.assertEqual(ask.call_args.args[1], root)

    def test_direct_question_uses_bound_counter_and_ne_filter_without_search_file(self):
        malicious = "Air'); DROP TABLE x; --"
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(tools, 'db', side_effect=[[ITEM], [ROW]]) as query, \
             patch.object(tools, 'add_documentation') as documentation, \
             patch.object(tools, 'explain') as explain, contextlib.redirect_stdout(io.StringIO()):
            root = Path(temporary)
            self.assertEqual(tools.ask_counter(arguments(counter=malicious, location=''), root, {}), 0)
            sql, params, _ = query.call_args_list[0].args
            self.assertNotIn(malicious, sql)
            self.assertEqual(params['counter'], malicious)
            self.assertIn('lowerUTF8({counter:String})', sql)
            self.assertIn("\\\\([^()]*\\\\)$", sql)
            sample_sql, sample_params, _ = query.call_args_list[1].args
            self.assertIn('match(lowerUTF8(r.ne_id), {ne_pattern:String})', sample_sql)
            self.assertEqual(sample_params['ne_pattern'], '(^|[^a-z0-9])adpf([^a-z0-9]|$)')
            self.assertEqual(sample_params['location'], '')
            self.assertEqual(documentation.call_args.kwargs['kind'], 'ADPF')
            self.assertEqual(explain.call_args.args[1]['question'], 'What does it measure?')
            self.assertFalse((root / 'run/counter-search.json').exists())

    def test_ambiguous_data_shows_choices_without_calling_model(self):
        output = io.StringIO()
        with patch.object(tools, 'db', return_value=[ITEM, dict(ITEM, family='Other')]), \
             patch.object(tools, 'explain') as explain, contextlib.redirect_stdout(output):
            self.assertEqual(tools.ask_counter(arguments(), Path('.'), {}), 1)
            explain.assert_not_called()
            self.assertIn('--family', output.getvalue())
            self.assertIn('Other', output.getvalue())

    def test_explicit_disambiguation_filters_are_bound(self):
        with patch.object(tools, 'db', return_value=[]) as query, \
             patch.object(tools, 'explain') as explain, contextlib.redirect_stdout(io.StringIO()):
            args = arguments(family='Air', data_version='1', schema_id='a' * 64)
            self.assertEqual(tools.ask_counter(args, Path('.'), {}), 1)
            sql, params, _ = query.call_args.args
            for key in ('family', 'data_version', 'schema_id'):
                self.assertIn('{' + key + ':String}', sql)
                self.assertEqual(params[key], getattr(args, key))
            explain.assert_not_called()

    def test_documentation_only_question_retrieves_real_definition(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / 'knowledge/counter-spec'
            directory.mkdir(parents=True)
            source = directory / 'ADPF.xlsx'
            source.write_bytes(b'private workbook fixture')
            import hashlib
            definition = {'ne_type': 'ADPF', 'counter': 'AirMac',
                          'fields': {'Family Name': 'Air', 'Type Description': 'Verified definition'},
                          'source': {'workbook': source.name, 'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                                     'sheet': 'counter', 'row': 10}}
            (directory / 'index.json').write_text(json.dumps({'records': [definition]}))
            with patch.object(tools, 'db', return_value=[]), patch.object(tools, 'explain') as explain, \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(tools.ask_counter(arguments(), root, {}), 0)
                evidence = explain.call_args.args[1]
                self.assertEqual(evidence['observations'], [])
                self.assertEqual(evidence['definitions'][0]['citation'], 'D1')

    def test_missing_evidence_does_not_call_model(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(tools, 'db', return_value=[]), \
             patch.object(tools, 'explain') as explain, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(tools.ask_counter(arguments(), Path(temporary), {}), 1)
            explain.assert_not_called()

    def test_question_and_sample_scope_are_present_in_model_prompt(self):
        evidence = tools.evidence_for(ITEM, [ROW])
        evidence['question'] = 'What is the maximum across the entire database?'
        captured = []
        def stream(request):
            captured.append(json.loads(request.data))
            return {'done': True, 'response': 'Only sample facts available [R1].'}
        with tempfile.TemporaryDirectory() as temporary, patch.object(tools, 'stream_explanation', stream), \
             contextlib.redirect_stdout(io.StringIO()):
            tools.explain(Path(temporary), evidence)
        self.assertIn(evidence['question'], captured[0]['prompt'])
        self.assertIn('Full-data totals', captured[0]['system'])
        self.assertIn('Never present sample minima', captured[0]['system'])


if __name__ == '__main__':
    unittest.main()
