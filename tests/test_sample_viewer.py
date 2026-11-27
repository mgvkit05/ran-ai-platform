import importlib.util
import io
from pathlib import Path
import unittest
import urllib.error
import urllib.parse

spec = importlib.util.spec_from_file_location('sample_viewer', Path(__file__).parents[1] / 'scripts' / '15_view_sample_data.py')
viewer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(viewer)


class ViewerTests(unittest.TestCase):
    def test_get_does_not_attempt_to_change_implicit_readonly_mode(self):
        testcase = self
        class Opener:
            def open(self, request, timeout):
                testcase.assertEqual(request.get_method(), 'GET')
                parsed = urllib.parse.urlsplit(request.full_url)
                testcase.assertEqual(parsed.netloc, '127.0.0.1:18123')
                parameters = urllib.parse.parse_qs(parsed.query)
                testcase.assertNotIn('readonly', parameters)
                testcase.assertEqual(parameters['query'], ['SELECT 1 FORMAT JSON'])
                return io.BytesIO(b'{"data":[{"value":1}]}')
        self.assertEqual(viewer.fetch(Opener(), {'user': 'test', 'password': 'secret'}, 'SELECT 1 FORMAT JSON'), [{'value': 1}])

    def test_http_failure_exposes_code_not_source_values(self):
        class Opener:
            def open(self, request, timeout):
                raise urllib.error.HTTPError(request.full_url, 500, 'error', {},
                      io.BytesIO(b"Code: 164. Cannot modify readonly (READONLY). SECRET_CELL_VALUE"))
        with self.assertRaises(RuntimeError) as error:
            viewer.fetch(Opener(), {'user': 'test', 'password': 'secret'}, 'SELECT 1')
        self.assertIn('164 (READONLY)', str(error.exception))
        self.assertNotIn('SECRET_CELL_VALUE', str(error.exception))

    def test_sample_bounds_and_controls(self):
        with self.assertRaises(ValueError):
            viewer.sample_sql(21, 3)
        self.assertIn('WHERE member_ordinal = 100', viewer.sample_sql(3, 3, 100))
        self.assertEqual(viewer.display(''), '""')
        self.assertNotIn('\x1b', viewer.display('\x1b[31m'))


if __name__ == '__main__':
    unittest.main()
