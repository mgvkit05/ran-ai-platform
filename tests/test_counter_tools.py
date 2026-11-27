import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.parse

spec = importlib.util.spec_from_file_location('counter_tools', Path(__file__).parents[1] / 'scripts' / '16_counter_tools.py')
tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tools)


def observation(value, entity='PRIVATE_ENTITY'):
    return {'raw_value': value, 'ne_id': entity, 'system_id': '01', 'ne_name': 'PRIVATE_NAME',
            'location': '', 'init_time_utc': '2026-01-01 00:00:00', 'gran_period_raw': '15'}


class CounterToolsTests(unittest.TestCase):
    def test_documentation_reaches_model_with_source_citation(self):
        item = {'counter_label': 'Synthetic(count)', 'unit_label': 'count', 'family': 'Synthetic', 'version': '1'}
        evidence = tools.evidence_for(item, [observation('14')])
        evidence['definitions'] = [{'citation': 'D1', 'fields': {'Type Description': 'Number of attempts'},
                                    'source': {'workbook': 'ACPF.xlsx', 'sheet': 'counter', 'row': 9}}]
        captured = []
        def transport(request):
            captured.append(json.loads(request.data))
            return {'done': True, 'response': 'Attempts [D1], sample 14 [R1].'}
        with tempfile.TemporaryDirectory() as temporary, patch.object(tools, 'stream_explanation', transport), contextlib.redirect_stdout(io.StringIO()):
            tools.explain(Path(temporary), evidence)
        self.assertIn('Number of attempts', captured[0]['prompt'])
        self.assertIn('[D1]', captured[0]['system'])
        self.assertNotIn('PRIVATE_ENTITY', captured[0]['prompt'])

    def test_search_lists_and_saves_more_than_ten_matches_by_default(self):
        catalog = [{'counter_label': 'Air%d' % i, 'unit_label': 'count',
                    'source_files': 1, 'schema_id': 'a' * 64,
                    'family': 'Air', 'version': '1'} for i in range(15)]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'ran-ai'
            (root / 'config/clickhouse-server').mkdir(parents=True)
            (root / 'config/clickhouse-server/credentials.json').write_text('{}')
            output = io.StringIO()
            with patch.object(tools.Path, 'home', return_value=Path(temporary)), \
                 patch('sys.argv', ['16_counter_tools.py', 'search', 'air']), \
                 patch.object(tools, 'db', return_value=catalog) as query, \
                 contextlib.redirect_stdout(output):
                self.assertEqual(tools.main(), 0)
            self.assertNotIn('LIMIT', query.call_args.args[0])
            self.assertEqual(query.call_args.args[1], {'term': 'air'})
            self.assertIn('[15]', output.getvalue())
            self.assertEqual(json.loads((root / 'run/counter-search.json').read_text()), catalog)

    def test_search_optional_limit_is_bound_and_accepts_more_than_fifty(self):
        self.assertIn('LIMIT {limit:UInt32}', tools.search_sql(100))
        for limit in (0, -1, 4294967296):
            with self.assertRaises(ValueError):
                tools.search_sql(limit)

    def test_exact_decimal_extrema_and_empty_or_nonnumeric_values(self):
        result = tools.sample_summary([observation('2.00000000000000000001'), observation('2.00000000000000000002'),
                                       observation('0.00'), observation(''), observation('NaN')])
        self.assertEqual(result['minimum_raw'], '0.00')
        self.assertEqual(result['maximum_raw'], '2.00000000000000000002')
        self.assertEqual(result['zero_rows'], 1)
        self.assertEqual(result['empty_rows'], 1)
        self.assertEqual(result['other_rows'], 1)
        self.assertIn('bounded sample', result['scope'])

    def test_db_uses_bound_term_without_sql_interpolation(self):
        captured = []
        def transport(request, timeout):
            captured.append(request)
            return {'data': []}
        malicious = "x'); DROP TABLE anything; --"
        with patch.object(tools, 'http_json', transport):
            tools.db(tools.SEARCH_SQL, {'term': malicious, 'limit': 10}, {'user': 'test', 'password': 'secret'})
        request = captured[0]
        parsed = urllib.parse.urlsplit(request.full_url)
        parameters = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(request.get_method(), 'GET')
        self.assertEqual(parsed.netloc, '127.0.0.1:18123')
        self.assertNotIn('readonly', parameters)
        self.assertEqual(parameters['param_term'], [malicious])
        self.assertNotIn(malicious, parameters['query'][0])

    def test_scope_and_selection_do_not_mix_schemas_or_versions(self):
        item = {'schema_id': 'a' * 64, 'counter_ordinal': 2}
        self.assertEqual(tools.selection([item], 1), item)
        with self.assertRaises(ValueError):
            tools.selection([item], 0)
        sql = tools.samples_sql(True)
        for parameter in ('{schema:String}', '{family:String}', '{version:String}', '{location:String}'):
            self.assertIn(parameter, sql)
        self.assertNotIn('{location:String}', tools.samples_sql(False))

    def test_model_receives_only_bounded_evidence_and_no_entity_identifiers(self):
        item = {'counter_label': 'Synthetic(count)', 'unit_label': 'count', 'family': 'Synthetic', 'version': '1'}
        evidence = tools.evidence_for(item, [observation('0'), observation('1', 'OTHER_PRIVATE_ENTITY')])
        serialized = json.dumps(evidence)
        self.assertNotIn('PRIVATE_ENTITY', serialized)
        self.assertNotIn('PRIVATE_NAME', serialized)
        self.assertEqual(evidence['observations'][0]['entity'], 'entity-1')
        self.assertEqual(evidence['observations'][1]['entity'], 'entity-2')
        captured = []
        def transport(request):
            captured.append(request)
            return {'done': True, 'response': 'A bounded sample [R1].'}
        with tempfile.TemporaryDirectory() as temporary, patch.object(tools, 'stream_explanation', transport), contextlib.redirect_stdout(io.StringIO()):
            tools.explain(Path(temporary), evidence)
        payload = json.loads(captured[0].data)
        self.assertEqual(captured[0].full_url, 'http://127.0.0.1:11435/api/generate')
        self.assertEqual(payload['keep_alive'], -1)
        self.assertIn('Do not execute tools or write SQL', payload['system'])
        self.assertNotIn('password', payload['prompt'])
        self.assertTrue(payload['stream'])
        self.assertEqual(payload['options']['num_ctx'], 2048)
        self.assertEqual(payload['options']['num_predict'], 192)

    def test_explanation_reuses_recovery_profile_without_batch_reload(self):
        evidence = tools.evidence_for({'counter_label': 'Test', 'unit_label': 'count', 'family': 'Test', 'version': '1'}, [observation('14')])
        captured = []
        def transport(request):
            captured.append(json.loads(request.data))
            return {'done': True, 'response': 'Bounded sample [R1].'}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'run').mkdir()
            (root / 'run/ollama-managed.json').write_text(json.dumps({'threads': 8, 'batch_size': 32}))
            with patch.object(tools, 'stream_explanation', transport), contextlib.redirect_stdout(io.StringIO()):
                tools.explain(root, evidence)
        self.assertEqual(captured[0]['options']['num_thread'], 8)
        self.assertEqual(captured[0]['options']['num_batch'], 32)

    def test_stream_reports_timings_without_printing_reasoning(self):
        events = [{'thinking': 'PRIVATE_REASONING'}, {'response': 'Sample [R1].'},
                  {'done': True, 'eval_count': 4, 'eval_duration': 1000000000, 'done_reason': 'stop'}]
        class Opener:
            def open(self, request, timeout):
                return io.BytesIO(('\n'.join(json.dumps(event) for event in events) + '\n').encode())
        output = io.StringIO()
        with patch.object(tools.urllib.request, 'build_opener', return_value=Opener()), contextlib.redirect_stdout(output):
            result = tools.stream_explanation(None)
        self.assertEqual(result['response'], 'Sample [R1].')
        self.assertNotIn('PRIVATE_REASONING', output.getvalue())
        self.assertIn('Timings:', output.getvalue())


if __name__ == '__main__':
    unittest.main()
