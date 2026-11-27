#!/usr/bin/env python3
"""Load all vendor TAR CSV records into ClickHouse without discarding blanks.
Save on the AI server as: /home/sysadmin/ran-ai/app/14_ingest_vendor_tar.py
Run from any directory, without sudo, while ClickHouse is running:
  "$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/14_ingest_vendor_tar.py"
The newest completed TAR in landing is selected automatically.
Uses only the standard library.
"""
import argparse
from collections import Counter
import csv
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import tarfile
import urllib.error
import urllib.request
import uuid

COMMON = ('NE_ID', 'SYSTEM_ID', 'NE_NAME', 'INIT_TIME', 'TIME_OFFSET', 'GRAN_PERIOD', 'LOCATION')
ROWS = 'ran_ai.ingest_rows_v1'
FILES = 'ran_ai.ingest_files_v1'
COMMITS = 'ran_ai.ingest_commits_v1'
MAX_FILE = 128 * 1024 ** 2


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def utc_time(value, offset):
    match = re.fullmatch(r'([+-])(\d{2}):(\d{2})', offset.strip())
    if not match or int(match[2]) > 23 or int(match[3]) > 59:
        return None
    try:
        minutes = int(match[2]) * 60 + int(match[3])
        zone = timezone(timedelta(minutes=minutes if match[1] == '+' else -minutes))
        original = datetime.fromisoformat(value.strip())
        if original.tzinfo is not None:
            return None  # Never guess how two explicit offsets should be combined.
        return original.replace(tzinfo=zone).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')
    except ValueError:
        return None


def parse_csv(content):
    metadata = []
    values = {}
    headers = None
    records = []
    for record in csv.reader(io.StringIO(content.decode('utf-8-sig'), newline=''), strict=True):
        if not record:
            continue
        if headers is None and record[0].startswith('#'):
            metadata.append(record)
            key, separator, value = record[0][1:].partition(':')
            if separator:
                key = key.strip().lower()
                values[key] = ','.join([value] + record[1:])
                if key == 'attribute':
                    headers = [value] + record[1:]
                    normalize = lambda field: field.strip().upper().replace('_', '')
                    if tuple(map(normalize, headers[:7])) != tuple(map(normalize, COMMON)):
                        raise ValueError('unsupported_common_header_layout')
            continue
        if headers is None:
            raise ValueError('data_before_attribute_header')
        if len(record) != len(headers):
            raise ValueError('row_width_differs_from_header')
        records.append(record)
    if headers is None:
        raise ValueError('attribute_header_missing')
    # Absence, blank and populated index are three distinct states.
    index_present = 'index' in values
    return {'headers': headers, 'metadata_records': metadata, 'family': values.get('family', ''),
            'version': values.get('version', ''), 'index_present': int(index_present),
            'index_definition': values.get('index', ''), 'records': records}


class Database:
    def __init__(self, credentials):
        self.credentials = credentials
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def execute(self, sql, body=''):
        request = urllib.request.Request('http://127.0.0.1:18123/', data=(sql + '\n' + body).encode('utf-8'),
                headers={'X-ClickHouse-User': self.credentials['user'], 'X-ClickHouse-Key': self.credentials['password']})
        try:
            with self.opener.open(request, timeout=180) as response:
                return response.read().decode('utf-8').strip()
        except urllib.error.HTTPError as error:
            detail = error.read(8192).decode('utf-8', errors='replace')
            raise RuntimeError('ClickHouse HTTP %d: %s' % (error.code, detail)) from None


def initialize(db):
    statements = [
        'CREATE DATABASE IF NOT EXISTS ran_ai',
        '''CREATE TABLE IF NOT EXISTS %s (
          load_id String, archive_sha256 FixedString(64), member_ordinal UInt32,
          row_ordinal UInt64, schema_id FixedString(64), ne_id String, system_id String,
          ne_name String, init_time_raw String, time_offset_raw String, gran_period_raw String,
          location String, init_time_utc Nullable(DateTime64(6, 'UTC')), raw_values Array(String)
        ) ENGINE=MergeTree PARTITION BY substring(archive_sha256,1,2)
          ORDER BY (archive_sha256,load_id,member_ordinal,row_ordinal)''' % ROWS,
        '''CREATE TABLE IF NOT EXISTS %s (
          load_id String, archive_sha256 FixedString(64), member_ordinal UInt32,
          member_name String, content_sha256 FixedString(64), schema_id FixedString(64),
          family String, version String, index_present UInt8, index_definition String,
          headers Array(String), counter_names Array(String), counter_units Array(String),
          metadata_records Array(Array(String)), expected_rows UInt64
        ) ENGINE=MergeTree PARTITION BY substring(archive_sha256,1,2)
          ORDER BY (archive_sha256,load_id,member_ordinal)''' % FILES,
        '''CREATE TABLE IF NOT EXISTS %s (
          archive_sha256 FixedString(64), load_id String, archive_name String,
          expected_files UInt64, expected_rows UInt64, expected_counter_fields UInt64,
          committed_utc DateTime('UTC')
        ) ENGINE=MergeTree ORDER BY (archive_sha256,load_id)''' % COMMITS,
        'CREATE VIEW IF NOT EXISTS ran_ai.counter_records AS SELECT * FROM %s WHERE load_id IN (SELECT load_id FROM %s)' % (ROWS, COMMITS),
        'CREATE VIEW IF NOT EXISTS ran_ai.counter_files AS SELECT * FROM %s WHERE load_id IN (SELECT load_id FROM %s)' % (FILES, COMMITS),
    ]
    for statement in statements:
        db.execute(statement)


class Batch:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.lines = []
        self.size = 0

    def add(self, record):
        text = json.dumps(record, ensure_ascii=True, allow_nan=False, separators=(',', ':'))
        size = len(text) + 1  # ASCII JSON: character count equals UTF-8 byte count.
        if self.lines and (self.size + size > 2 * 1024 ** 2 or len(self.lines) >= 2000):
            self.flush()
        self.lines.append(text)
        self.size += size

    def flush(self):
        if self.lines:
            # No automatic retries: an ambiguous write must not duplicate source rows.
            self.db.execute('INSERT INTO %s FORMAT JSONEachRow' % self.table, '\n'.join(self.lines) + '\n')
            self.lines, self.size = [], 0


def ingest(path, db, emit=print):
    before = path.stat()
    archive_hash = digest(path)
    initialize(db)
    committed = int(db.execute("SELECT count() FROM %s WHERE archive_sha256='%s'" % (COMMITS, archive_hash)))
    if committed:
        return {'already_committed': True, 'files': 0, 'rows': 0}
    load_id = uuid.uuid4().hex
    row_batch, file_batch = Batch(db, ROWS), Batch(db, FILES)
    totals = Counter(files=0, rows=0, counter_fields=0, blank_locations=0,
                     blank_index_files=0, absent_index_files=0, utc_unavailable=0)
    seen = set()
    expanded_bytes = 0
    try:
        with tarfile.open(path, 'r|*') as archive:
            for ordinal, member in enumerate(archive, 1):
                if ordinal > 10000:
                    raise ValueError('member_limit')
                member_path = PurePosixPath(member.name)
                if member_path.is_absolute() or '..' in member_path.parts or '\\' in member.name or ':' in member.name:
                    raise ValueError('unsafe_archive_path')
                if member.isdir():
                    continue
                if not member.isfile():
                    raise ValueError('unsupported_special_member')
                if str(member_path) in seen:
                    raise ValueError('duplicate_member_name')
                seen.add(str(member_path))
                if not member.name.lower().endswith(('.csv', '.csv.gz')):
                    raise ValueError('non_csv_member_requires_mapping')
                if member.size > MAX_FILE:
                    raise ValueError('member_size_limit')
                with archive.extractfile(member) as incoming:
                    if member.name.lower().endswith('.gz'):
                        with gzip.GzipFile(fileobj=incoming) as source:
                            content = source.read(MAX_FILE + 1)
                    else:
                        content = incoming.read(MAX_FILE + 1)
                if len(content) > MAX_FILE:
                    raise ValueError('decompressed_size_limit')
                expanded_bytes += len(content)
                if expanded_bytes > 2 * 1024 ** 3:
                    raise ValueError('expanded_archive_size_limit')
                parsed = parse_csv(content)
                headers = parsed['headers']
                schema = hashlib.sha256(json.dumps(headers, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()
                counters = headers[7:]
                units = []
                for header in counters:
                    match = re.search(r'\(([^()]*)\)\s*$', header)
                    units.append(match[1] if match else '')
                file_record = {key: parsed[key] for key in ('family', 'version', 'index_present', 'index_definition', 'metadata_records')}
                file_record.update(load_id=load_id, archive_sha256=archive_hash, member_ordinal=ordinal,
                        member_name=member.name, content_sha256=hashlib.sha256(content).hexdigest(),
                        schema_id=schema, headers=headers, counter_names=counters, counter_units=units,
                        expected_rows=len(parsed['records']))
                file_batch.add(file_record)
                totals['files'] += 1
                totals['absent_index_files'] += int(not parsed['index_present'])
                totals['blank_index_files'] += int(bool(parsed['index_present']) and not parsed['index_definition'].strip())
                for row_number, record in enumerate(parsed['records'], 1):
                    timestamp = utc_time(record[3], record[4])
                    row_batch.add(dict(load_id=load_id, archive_sha256=archive_hash, member_ordinal=ordinal,
                            row_ordinal=row_number, schema_id=schema, ne_id=record[0], system_id=record[1],
                            ne_name=record[2], init_time_raw=record[3], time_offset_raw=record[4],
                            gran_period_raw=record[5], location=record[6], init_time_utc=timestamp,
                            raw_values=record))
                    totals['rows'] += 1
                    totals['counter_fields'] += len(record) - 7
                    totals['blank_locations'] += int(not record[6].strip())
                    totals['utc_unavailable'] += int(timestamp is None)
                if totals['files'] % 100 == 0:
                    emit('Staged CSVs: %d; source rows: %d' % (totals['files'], totals['rows']))
        if not totals['files']:
            raise ValueError('no_csv_files')
        row_batch.flush()
        file_batch.flush()
        after = path.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns) or digest(path) != archive_hash:
            raise ValueError('source_archive_changed')
        stored_rows = json.loads(db.execute("SELECT count() AS n, uniqExact(tuple(member_ordinal,row_ordinal)) AS unique_n, sum(length(raw_values)-7) AS counters FROM %s WHERE load_id='%s' FORMAT JSON" % (ROWS, load_id)))['data'][0]
        stored_files = json.loads(db.execute("SELECT count() AS n, uniqExact(member_ordinal) AS unique_n, sum(expected_rows) AS expected_n FROM %s WHERE load_id='%s' FORMAT JSON" % (FILES, load_id)))['data'][0]
        if (int(stored_rows['n']) != totals['rows'] or int(stored_rows['unique_n']) != totals['rows']
                or int(stored_rows['counters'] or 0) != totals['counter_fields']
                or int(stored_files['n']) != totals['files'] or int(stored_files['unique_n']) != totals['files']
                or int(stored_files['expected_n']) != totals['rows']):
            raise ValueError('stored_counts_do_not_match_source')
        manifest = dict(archive_sha256=archive_hash, load_id=load_id, archive_name=path.name,
                expected_files=totals['files'], expected_rows=totals['rows'],
                expected_counter_fields=totals['counter_fields'],
                committed_utc=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'))
        final = Batch(db, COMMITS)
        final.add(manifest)
        final.flush()  # Publication point: views show only committed load IDs.
        if int(db.execute("SELECT count() FROM %s WHERE load_id='%s'" % (COMMITS, load_id))) != 1:
            raise ValueError('commit_marker_not_confirmed')
        result = dict(totals)
        result['already_committed'] = False
        return result
    except BaseException:
        emit('Load did not finish normally. On retry the commit ledger is checked first; uncommitted attempts stay hidden from the public views.')
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', nargs='?', type=Path)
    args = parser.parse_args()
    if not sys_platform_linux():
        parser.error('run on the Linux AI server')
    os.umask(0o077)
    csv.field_size_limit(1024 * 1024)
    root = Path.home() / 'ran-ai'
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-%d' % os.getpid()
    try:
        import fcntl
        with (root / 'run' / 'vendor-ingest.lock').open('a') as lock, (root / 'logs' / ('14-ingest-' + stamp + '.log')).open('x', encoding='utf-8') as log:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            def emit(message):
                print(message, flush=True)
                log.write(message + '\n')
                log.flush()
            try:
                if os.geteuid() == 0:
                    raise ValueError('run_without_sudo')
                landing = (root / 'data' / 'landing').resolve()
                choices = [item for item in landing.iterdir() if item.is_file() and not item.is_symlink()
                           and item.name.lower().endswith(('.tar', '.tgz', '.tar.gz', '.tar.bz2', '.tar.xz'))]
                if args.archive is None:
                    if not choices:
                        raise ValueError('no_completed_tar')
                    path = max(choices, key=lambda item: item.stat().st_mtime_ns)
                else:
                    path = args.archive if args.archive.is_absolute() else landing / args.archive
                if path.is_symlink():
                    raise ValueError('archive_symlink')
                path.resolve().relative_to(landing)
                credentials = json.loads((root / 'config/clickhouse-server/credentials.json').read_text())
                emit('Loading the complete archive, preserving blank index/location and original string values.')
                result = ingest(path, Database(credentials), emit)
                if result['already_committed']:
                    emit('ALREADY LOADED: this exact TAR is committed; no data rows inserted again.')
                else:
                    emit('SUCCESS: archive published to ran_ai.counter_records and ran_ai.counter_files.')
                    for key in ('files', 'rows', 'counter_fields', 'blank_locations', 'blank_index_files', 'absent_index_files', 'utc_unavailable'):
                        emit('%s: %d' % (key, result[key]))
                emit('Original TAR retained. No source values or identifiers printed. Operational log saved under project logs.')
                return 0
            except (Exception, KeyboardInterrupt) as error:
                # Detailed DB errors may contain values: retain privately, show only the class.
                log.write('PRIVATE ERROR DETAIL: %s: %s\n' % (type(error).__name__, error))
                code = str(error) if isinstance(error, ValueError) and re.fullmatch('[a-z_]+', str(error)) else type(error).__name__
                print('LOAD STOPPED: %s. Original TAR retained. Review the local log before sharing details.' % code)
                return 1
    except OSError as error:
        print('Cannot open project lock/log: %s' % type(error).__name__)
        return 1


def sys_platform_linux():
    import sys
    return sys.platform.startswith('linux')


if __name__ == '__main__':
    raise SystemExit(main())
