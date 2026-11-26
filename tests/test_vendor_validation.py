import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('vendor_validation', Path(__file__).parents[1] / 'scripts' / '12_validate_vendor_tar.py')
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)

HEADER = ('#family:Synthetic family\n#version:1.0\n#index:Cell/Test\n'
          '#attribute:NE_ID,SYSTEM_ID,NE_NAME,INIT_TIME,TIME_OFFSET,GRAN_PERIOD,LOCATION,metric_A(count),metric_B(kbps)\n')
ROW = 'NE_TEST,001,Name_TEST,2026-01-01 10:15:00,+00:00,15,Cell_TEST,1.25,2e3\n'


class VendorValidationTests(unittest.TestCase):
    def test_vendor_metadata_and_all_rows_parsed(self):
        result = validator.validate_csv((HEADER + ROW * 250).encode())
        self.assertEqual(result['rows'], 250)
        self.assertEqual(result['numeric_values'], 500)
        self.assertEqual(result['metadata']['family'], 'Synthetic family')
        self.assertEqual(result['issues'], {})
        self.assertNotIn('NE_TEST', json.dumps(result))
        self.assertEqual(result['periods_raw'], {'15': 250})

    def test_timestamp_calendar_and_timezone_offset_checked(self):
        self.assertTrue(validator.validate_time('2026-01-01 00:00:00', '-05:30'))
        self.assertFalse(validator.validate_time('2026-02-30 00:00:00', '+00:00'))
        self.assertFalse(validator.validate_time('2026-01-01 00:00:00', '+24:00'))
        self.assertFalse(validator.validate_time('2026-01-01', '+00:00'))

    def test_width_timestamp_and_counter_errors_are_counted(self):
        invalid = ROW.replace('2026-01-01', '2026-02-30').replace('1.25', 'NaN')
        result = validator.validate_csv((HEADER + invalid + 'short,row\n').encode())
        self.assertEqual(result['invalid_rows'], 2)
        self.assertEqual(result['issues']['row_width_mismatch'], 1)
        self.assertEqual(result['issues']['invalid_timestamp_or_offset'], 1)
        self.assertEqual(result['issues']['nonnumeric_or_nonfinite_counter_value'], 1)

    def test_header_alias_and_empty_counter(self):
        result = validator.validate_csv((HEADER.replace('NE_NAME', 'NENAME') + ROW.replace('1.25', '')).encode())
        self.assertEqual(result['issues'], {})
        self.assertEqual(result['empty_counter_values'], 1)

    def test_archive_summary_is_short_and_private(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'test.tar'
            content = (HEADER + ROW).encode()
            with tarfile.open(path, 'w') as archive:
                entry = tarfile.TarInfo('confidential_filename.csv')
                entry.size = len(content)
                archive.addfile(entry, io.BytesIO(content))
            report = validator.validate_archive(path)
            text, ready = validator.summary(report)
            self.assertTrue(ready)
            self.assertLess(len(text.splitlines()), 15)
            for private in ('confidential_filename', 'Synthetic family', 'NE_TEST', 'metric_A', '2026-01-01'):
                self.assertNotIn(private, text)
            self.assertEqual([item.name for item in root.iterdir()], ['test.tar'])


if __name__ == '__main__':
    unittest.main()
