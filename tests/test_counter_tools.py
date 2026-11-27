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
        def transport(request, timeout):
            captured.append(request)
            return {'done': True, 'response': 'A bounded sample [R1].'}
        with tempfile.TemporaryDirectory() as temporary, patch.object(tools, 'http_json', transport), contextlib.redirect_stdout(io.StringIO()):
            tools.explain(Path(temporary), evidence)
        payload = json.loads(captured[0].data)
        self.assertEqual(captured[0].full_url, 'http://127.0.0.1:11435/api/generate')
        self.assertEqual(payload['keep_alive'], -1)
        self.assertIn('Do not execute tools or write SQL', payload['system'])
        self.assertNotIn('password', payload['prompt'])


if __name__ == '__main__':
    unittest.main()
