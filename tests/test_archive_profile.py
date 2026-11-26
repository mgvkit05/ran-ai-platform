import gzip
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location('archive_profile', Path(__file__).parents[1] / 'scripts' / '11_profile_archive.py')
profiler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profiler)


class ArchiveProfileTests(unittest.TestCase):
    def test_tar_csv_and_gzip_are_read_without_extraction_or_value_disclosure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'sample.tar'
            content = b'vendor_preamble\nstart_time,cell_id,private_counter\n2026-11-26,SECRET_CELL,987654321\n'
            with tarfile.open(archive, 'w') as target:
                for name, data in [('sensitive_name.csv', content), ('folder/other.csv.gz', gzip.compress(content))]:
                    entry = tarfile.TarInfo(name)
                    entry.size = len(data)
                    target.addfile(entry, io.BytesIO(data))
                link = tarfile.TarInfo('escape.csv')
                link.type = tarfile.SYMTYPE
                link.linkname = '/outside'
                target.addfile(link)
            result = profiler.profile(archive)
            self.assertEqual(len(result['csv_profiled']), 2)
            self.assertEqual(result['unsafe_or_link_members_skipped'], 1)
            self.assertEqual(result['csv_profiled'][0]['header_record_candidate'], 2)
            self.assertEqual(result['csv_profiled'][0]['sampled_rows'], 1)
            summary = profiler.sanitized(result)
            for secret in ('SECRET_CELL', '987654321', 'sensitive_name', 'private_counter', 'start_time', 'cell_id'):
                self.assertNotIn(secret, summary)
            self.assertNotIn('SECRET_CELL', json.dumps(result))
            self.assertEqual([path.name for path in root.iterdir()], ['sample.tar'])

    def test_zip_and_semicolon_delimiter(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'sample.zip'
            with zipfile.ZipFile(archive, 'w') as target:
                target.writestr('sample.csv', 'cell;attempts;successes\nX;100;98\n')
            result = profiler.profile(archive)
            self.assertEqual(result['archive_type'], 'zip')
            self.assertEqual(result['csv_profiled'][0]['delimiter'], ';')
            self.assertEqual(result['csv_profiled'][0]['columns'][1]['sample_type'], 'numeric')

    def test_unsafe_paths_rejected(self):
        for name in ('../escape.csv', '/absolute.csv', 'folder\\escape.csv'):
            self.assertFalse(profiler.safe_name(name))
        self.assertTrue(profiler.safe_name('./folder/file.csv'))
        self.assertTrue(profiler.supported(Path('sample.tar')))
        self.assertFalse(profiler.supported(Path('sample.tar.part')))

    def test_terminal_schema_summary_is_bounded(self):
        result = {'archive_type': 'tar', 'members_seen': 2, 'csv_members_seen': 2,
                  'csv_profiled': [profiler.describe(b'a,b\n1,2\n'), profiler.describe(b'c,d,e\n1,2,3\n')],
                  'unsafe_or_link_members_skipped': 0, 'member_profile_errors': 0, 'sample_budget_reached': False}
        summary = profiler.sanitized(result, 1)
        self.assertIn('Schema-001:', summary)
        self.assertNotIn('Schema-002:', summary)
        self.assertIn('Additional schemas omitted from terminal: 1', summary)


if __name__ == '__main__':
    unittest.main()
