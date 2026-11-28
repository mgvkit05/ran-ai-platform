import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.parse

spec = importlib.util.spec_from_file_location('nonzero', Path(__file__).parents[1] / 'scripts/19_view_nonzero_counters.py')
viewer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(viewer)


class NonzeroCounterTests(unittest.TestCase):
    def test_query_joins_committed_files_and_preserves_raw_counter_offset(self):
        sql, parameters = viewer.query()
        self.assertIn('ran_ai.counter_records', sql)
        self.assertIn('ran_ai.counter_files', sql)
        self.assertIn('r.load_id=f.load_id AND r.member_ordinal=f.member_ordinal', sql)
        self.assertIn('r.raw_values[7 + counter_position]', sql)
        self.assertIn('isFinite(toFloat64OrNull(value_raw))', sql)
        self.assertIn('toFloat64OrNull(value_raw) != 0', sql)
        self.assertEqual(parameters, {'limit': 10})

    def test_filters_are_bound_and_invalid_limits_rejected(self):
        value = "x'); DROP TABLE x; --"
        sql, parameters = viewer.query(25, value, value)
        self.assertNotIn(value, sql)
        self.assertEqual(parameters['counter'], value)
        self.assertEqual(parameters['ne_id'], value)
        for rows in (0, -1, 1001):
            with self.assertRaises(ValueError):
                viewer.query(rows)

    def test_transport_is_local_read_only_get_and_does_not_override_readonly(self):
        captured = []
        class Response(io.BytesIO):
            pass
        class Opener:
            def open(self, request, timeout):
                captured.append(request)
                return Response(b'{"data":[]}')
        with patch.object(viewer.urllib.request, 'build_opener', return_value=Opener()):
            sql, params = viewer.query()
            self.assertEqual(viewer.fetch({'user': 'user', 'password': 'secret'}, sql, params), [])
        request = captured[0]
        parsed = urllib.parse.urlsplit(request.full_url)
        self.assertEqual(parsed.netloc, '127.0.0.1:18123')
        self.assertEqual(request.get_method(), 'GET')
        self.assertNotIn('secret', request.full_url)
        self.assertNotIn('readonly', urllib.parse.parse_qs(parsed.query))

    def test_cli_prints_real_result_strings_and_defaults_to_ten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / 'ran-ai/config/clickhouse-server/credentials.json'
            path.parent.mkdir(parents=True)
            path.write_text('{}')
            result = {'counter_name': 'Example(count)', 'value_raw': '00014.0000', 'location': ''}
            output = io.StringIO()
            with patch.object(viewer.Path, 'home', return_value=root), patch('sys.argv', ['19_view_nonzero_counters.py']), \
                 patch.object(viewer, 'fetch', return_value=[result]) as fetch, contextlib.redirect_stdout(output):
                self.assertEqual(viewer.main(), 0)
            self.assertEqual(fetch.call_args.args[2]['limit'], 10)
            self.assertIn('00014.0000', output.getvalue())
            self.assertIn('Displayed: 1', output.getvalue())


if __name__ == '__main__':
    unittest.main()
