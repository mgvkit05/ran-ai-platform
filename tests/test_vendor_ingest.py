import importlib.util
import io
import json
from pathlib import Path
import re
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('vendor_ingest', Path(__file__).parents[1] / 'scripts' / '14_ingest_vendor_tar.py')
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)

HEADER = ('#family:Synthetic\n#version:1\n#index:\n'
          '#attribute:NE_ID,SYSTEM_ID,NE_NAME,INIT_TIME,TIME_OFFSET,GRAN_PERIOD,LOCATION,metric(count)\n')
ROW = '001,0002,Test,2026-01-01 10:15:00,+02:00,15,,2.00000000000000000001\n'


class FakeDatabase:
    """Simulate write ambiguity and publication without claiming SQL-engine coverage."""
    def __init__(self):
        self.tables = {loader.ROWS: [], loader.FILES: [], loader.COMMITS: []}
        self.sql = []
        self.fail_next_row_insert = False
        self.bad_counts = False

    def execute(self, sql, body=''):
        self.sql.append(sql)
        if sql.startswith('CREATE '):
            return ''
        if sql.startswith('INSERT'):
            table = sql.split()[2]
            values = [json.loads(line) for line in body.splitlines()]
            if table == loader.ROWS and self.fail_next_row_insert:
                self.fail_next_row_insert = False
                self.tables[table].append(values[0])  # A partial/ambiguous write.
                raise RuntimeError('simulated connection loss')
            self.tables[table].extend(values)
            return ''
        identifier = re.search(r"(?:archive_sha256|load_id)='([a-f0-9]+)'", sql).group(1)
        if sql.startswith('SELECT count() FROM ' + loader.COMMITS):
            key = 'archive_sha256' if 'WHERE archive_sha256' in sql else 'load_id'
            return str(sum(item[key] == identifier for item in self.tables[loader.COMMITS]))
        table = loader.ROWS if 'FROM ' + loader.ROWS in sql else loader.FILES
        records = [record for record in self.tables[table] if record['load_id'] == identifier]
        if table == loader.ROWS:
            result = {'n': len(records) + int(self.bad_counts),
                      'unique_n': len({(r['member_ordinal'], r['row_ordinal']) for r in records}),
                      'counters': sum(len(r['raw_values']) - 7 for r in records)}
        else:
            result = {'n': len(records), 'unique_n': len({r['member_ordinal'] for r in records}),
                      'expected_n': sum(r['expected_rows'] for r in records)}
        return json.dumps({'data': [result]})

    def visible_rows(self):
        complete = {record['load_id'] for record in self.tables[loader.COMMITS]}
        return [record for record in self.tables[loader.ROWS] if record['load_id'] in complete]


def make_archive(path, rows=1):
    with tarfile.open(path, 'w') as archive:
        for index, header in enumerate((HEADER, HEADER.replace('#index:\n', ''))):
            content = (header + ROW * rows).encode()
            member = tarfile.TarInfo('synthetic_%d.csv' % index)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))


class VendorIngestTests(unittest.TestCase):
    def test_blanks_absence_precision_and_offsets_preserved(self):
        blank = loader.parse_csv((HEADER + ROW).encode())
        absent = loader.parse_csv((HEADER.replace('#index:\n', '') + ROW).encode())
        self.assertEqual(blank['index_present'], 1)
        self.assertEqual(absent['index_present'], 0)
        self.assertEqual(blank['index_definition'], '')
        self.assertEqual(blank['records'][0][0], '001')
        self.assertEqual(blank['records'][0][6], '')
        self.assertEqual(blank['records'][0][7], '2.00000000000000000001')
        self.assertEqual(loader.utc_time('2026-01-01 10:15:00', '+02:00'), '2026-01-01 08:15:00.000000')
        self.assertIsNone(loader.utc_time('unrecognized', '+00:00'))

    def test_archive_commit_and_repeated_run_are_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sample.tar'
            make_archive(path)
            db = FakeDatabase()
            result = loader.ingest(path, db, lambda message: None)
            self.assertEqual(result['files'], 2)
            self.assertEqual(result['rows'], 2)
            self.assertEqual(result['blank_locations'], 2)
            self.assertEqual(result['blank_index_files'], 1)
            self.assertEqual(result['absent_index_files'], 1)
            self.assertEqual(len(db.visible_rows()), 2)
            self.assertTrue(loader.ingest(path, db, lambda message: None)['already_committed'])
            self.assertEqual(len(db.tables[loader.ROWS]), 2)
            self.assertFalse(any(sql.startswith(('DELETE', 'DROP', 'TRUNCATE')) for sql in db.sql))

    def test_failed_write_hidden_and_retry_publishes_only_complete_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sample.tar'
            make_archive(path, rows=1100)
            db = FakeDatabase()
            db.fail_next_row_insert = True
            with self.assertRaisesRegex(RuntimeError, 'connection loss'):
                loader.ingest(path, db, lambda message: None)
            self.assertEqual(len(db.visible_rows()), 0)
            self.assertEqual(len(db.tables[loader.ROWS]), 1)
            result = loader.ingest(path, db, lambda message: None)
            self.assertEqual(result['rows'], 2200)
            self.assertEqual(len(db.visible_rows()), 2200)
            self.assertEqual(len(db.tables[loader.ROWS]), 2201)

    def test_count_mismatch_does_not_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sample.tar'
            make_archive(path)
            db = FakeDatabase()
            db.bad_counts = True
            with self.assertRaisesRegex(ValueError, 'stored_counts_do_not_match_source'):
                loader.ingest(path, db, lambda message: None)
            self.assertEqual(db.tables[loader.COMMITS], [])
            self.assertEqual(db.visible_rows(), [])

    def test_empty_csv_records_are_not_coerced_or_dropped(self):
        row = ',,,,,,,\n'
        parsed = loader.parse_csv((HEADER + row).encode())
        self.assertEqual(parsed['records'], [[''] * 8])


if __name__ == '__main__':
    unittest.main()
