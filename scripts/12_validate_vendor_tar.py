#!/usr/bin/env python3
"""Read-only full-row vendor TAR validation; no extraction or database writes.
Run: "$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/12_validate_vendor_tar.py"
"""
import argparse
from collections import Counter
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import gzip
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import tarfile
import zlib

COMMON = ('NE_ID', 'SYSTEM_ID', 'NE_NAME', 'INIT_TIME', 'TIME_OFFSET', 'GRAN_PERIOD', 'LOCATION')
MAX_MEMBER_BYTES = 128 * 1024 ** 2
MAX_TOTAL_BYTES = 2 * 1024 ** 3


def normalized(name):
    return name.strip().upper().replace('_', '')


def validate_time(value, offset):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?', value):
        return False
    match = re.fullmatch(r'([+-])(\d{2}):(\d{2})', offset)
    if not match or int(match[2]) > 23 or int(match[3]) > 59:
        return False
    try:
        minutes = int(match[2]) * 60 + int(match[3])
        zone = timezone(timedelta(minutes=minutes if match[1] == '+' else -minutes))
        datetime.fromisoformat(value).replace(tzinfo=zone).astimezone(timezone.utc)
        return True
    except ValueError:
        return False


def validate_csv(content):
    result = {'metadata': {}, 'headers': [], 'rows': 0, 'invalid_rows': 0,
              'numeric_values': 0, 'empty_counter_values': 0, 'periods_raw': {}, 'issues': {},
              'empty_common_fields': {}, 'index_record_status': 'absent'}
    issues = Counter()
    periods = Counter()
    empty_fields = Counter()
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        result['issues'] = {'unsupported_encoding': 1}
        return result
    reader = csv.reader(io.StringIO(text, newline=''), strict=True)
    header = None
    prefix_valid = False
    try:
        for row in reader:
            if not row or all(not value.strip() for value in row):
                continue
            if row[0].startswith('#'):
                key, separator, value = row[0][1:].partition(':')
                if not separator:
                    issues['unrecognized_metadata_record'] += 1
                    continue
                key = key.strip().lower()
                if header is not None:
                    issues['metadata_after_header'] += 1
                    continue
                if key == 'attribute':
                    header = [value.strip()] + [field.strip() for field in row[1:]]
                    result['headers'] = header
                    prefix_valid = tuple(normalized(name) for name in header[:7]) == tuple(normalized(name) for name in COMMON)
                    if not prefix_valid:
                        issues['unexpected_common_columns'] += 1
                    if len(header) < 8:
                        issues['no_counter_columns'] += 1
                    if any(not name for name in header):
                        issues['blank_header'] += 1
                    if len(set(header)) != len(header):
                        issues['duplicate_header'] += 1
                elif key in ('family', 'version', 'index'):
                    if key in result['metadata']:
                        issues['duplicate_metadata_key'] += 1
                    result['metadata'][key] = ','.join([value] + row[1:]).strip()
                    if key == 'index':
                        result['index_record_status'] = 'nonempty' if result['metadata'][key] else 'blank'
                else:
                    issues['unrecognized_metadata_key'] += 1
                continue
            if header is None:
                issues['data_before_header'] += 1
                continue
            result['rows'] += 1
            if len(row) != len(header):
                result['invalid_rows'] += 1
                issues['row_width_mismatch'] += 1
                continue
            if not prefix_valid:
                result['invalid_rows'] += 1
                continue
            invalid = False
            missing_fields = [COMMON[index] for index, value in enumerate(row[:7]) if not value.strip()]
            if missing_fields:
                empty_fields.update(missing_fields)
                issues['empty_common_field'] += 1
                invalid = True
            if not validate_time(row[3].strip(), row[4].strip()):
                issues['invalid_timestamp_or_offset'] += 1
                invalid = True
            try:
                period = int(row[5].strip())
                if period <= 0:
                    raise ValueError()
                periods[str(period)] += 1
            except ValueError:
                issues['invalid_collection_period'] += 1
                invalid = True
            for value in row[7:]:
                value = value.strip()
                if not value:
                    result['empty_counter_values'] += 1
                    continue
                try:
                    if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', value):
                        raise InvalidOperation()
                    number = Decimal(value)
                    if not number.is_finite():
                        raise InvalidOperation()
                    result['numeric_values'] += 1
                except InvalidOperation:
                    issues['nonnumeric_or_nonfinite_counter_value'] += 1
                    invalid = True
            result['invalid_rows'] += int(invalid)
    except csv.Error:
        issues['csv_parse_error'] += 1
    if header is None:
        issues['missing_attribute_header'] += 1
    for key in ('family', 'version', 'index'):
        if not result['metadata'].get(key):
            issues['missing_' + key] += 1
    result['periods_raw'] = dict(periods)
    result['empty_common_fields'] = dict(empty_fields)
    result['issues'] = dict(issues)
    return result


def validate_archive(path):
    report = {'csvs': [], 'members': 0, 'unsafe_or_special_members': 0, 'non_csv_files': 0}
    total = 0
    names = set()
    before = path.stat()
    with tarfile.open(path, 'r|*') as archive:
        for member in archive:
            report['members'] += 1
            if report['members'] > 10000:
                raise ValueError('member_limit')
            pure = PurePosixPath(member.name)
            if pure.is_absolute() or '..' in pure.parts or '\\' in member.name or ':' in member.name or not (member.isdir() or member.isfile()):
                report['unsafe_or_special_members'] += 1
                continue
            if member.isdir():
                continue
            if not member.name.lower().endswith(('.csv', '.csv.gz')):
                report['non_csv_files'] += 1
                continue
            if str(pure) in names:
                raise ValueError('duplicate_member_name')
            names.add(str(pure))
            if member.size > MAX_MEMBER_BYTES:
                raise ValueError('member_size_limit')
            with archive.extractfile(member) as incoming:
                if member.name.lower().endswith('.gz'):
                    with gzip.GzipFile(fileobj=incoming) as source:
                        data = source.read(MAX_MEMBER_BYTES + 1)
                else:
                    data = incoming.read(MAX_MEMBER_BYTES + 1)
            if len(data) > MAX_MEMBER_BYTES:
                raise ValueError('decompressed_member_size_limit')
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise ValueError('total_size_limit')
            item = validate_csv(data)
            item['member'] = member.name  # Stored in the local private report only.
            report['csvs'].append(item)
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('archive_changed')
    return report


def summary(report):
    files = report['csvs']
    issues = Counter()
    periods = Counter()
    schemas = set()
    headers = set()
    index_status = Counter()
    empty_fields = Counter()
    empty_field_files = Counter()
    missing_index_rows = 0
    only_location_missing = 0
    for item in files:
        issues.update(item['issues'])
        periods.update(item['periods_raw'])
        schemas.add((item['metadata'].get('family'), item['metadata'].get('version'), tuple(item['headers'])))
        headers.add(tuple(item['headers']))
        index_status[item.get('index_record_status', 'unknown')] += 1
        empty_fields.update(item.get('empty_common_fields', {}))
        empty_field_files.update(item.get('empty_common_fields', {}).keys())
        if item.get('index_record_status') in ('absent', 'blank'):
            missing_index_rows += item['rows']
            if set(item.get('empty_common_fields', {})) == {'LOCATION'}:
                only_location_missing += 1
    lines = ['=== Full-row TAR validation (no filenames, headers or row values) ===',
             'CSV files: %d; candidate family/version/header combinations: %d' % (len(files), len(schemas)),
             'Distinct exact header layouts: %d' % len(headers),
             'Files without validation issues: %d; files with issues: %d' %
             (sum(not item['issues'] for item in files), sum(bool(item['issues']) for item in files)),
             'Data rows: %d; invalid rows: %d' % (sum(item['rows'] for item in files), sum(item['invalid_rows'] for item in files)),
             'Numeric counter values: %d; empty counter fields: %d' %
             (sum(item['numeric_values'] for item in files), sum(item['empty_counter_values'] for item in files)),
             'Collection-period fields (raw integers, unit not assumed): ' + ', '.join(sorted(periods, key=int)),
             'Unsafe/special members skipped: %d; non-CSV files: %d' % (report['unsafe_or_special_members'], report['non_csv_files'])]
    lines.append('Issue counts: ' + (', '.join('%s=%d' % pair for pair in sorted(issues.items())) or 'none'))
    lines.append('Empty common fields (rows/files): ' + (', '.join('%s=%d/%d' % (name, empty_fields[name], empty_field_files[name]) for name in COMMON if empty_fields[name]) or 'none'))
    lines.append('Index records (files): nonempty=%d blank=%d absent=%d' % (index_status['nonempty'], index_status['blank'], index_status['absent']))
    lines.append('Blank/absent-index files: rows=%d; files where LOCATION is the only empty common field=%d' % (missing_index_rows, only_location_missing))
    ready = bool(files) and not issues and not report['unsafe_or_special_members']
    lines.append('STRUCTURAL VALIDATION: ' + ('PASS' if ready else 'REVIEW REQUIRED'))
    lines.append('Identifiers remain strings; timestamps plus offsets were checked. Period units and counter semantics still need confirmation.')
    return '\n'.join(lines), ready


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', nargs='?', type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    csv.field_size_limit(1024 * 1024)
    root = Path.home() / 'ran-ai'
    try:
        landing = (root / 'data/landing').resolve()
        candidates = [path for path in landing.iterdir() if path.is_file() and not path.is_symlink()
                      and path.name.lower().endswith(('.tar', '.tgz', '.tar.gz', '.tar.bz2', '.tar.xz'))]
        if args.archive is None:
            if not candidates:
                raise ValueError('no_completed_tar')
            path = max(candidates, key=lambda candidate: candidate.stat().st_mtime_ns)
        else:
            path = args.archive if args.archive.is_absolute() else landing / args.archive
        if path.is_symlink():
            raise ValueError('archive_symlink')
        path.resolve().relative_to(landing)
        report = validate_archive(path)
        text, ready = summary(report)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-%d' % os.getpid()
        for suffix, content in (('private.json', json.dumps(report, indent=2)), ('summary.txt', text + '\n')):
            with (root / 'reports' / ('12-validation-' + stamp + '-' + suffix)).open('x', encoding='utf-8') as output:
                output.write(content)
        with (root / 'logs' / ('12-validation-' + stamp + '.log')).open('x', encoding='utf-8') as output:
            output.write(text + '\n')
        print(text)
        print('Share this short summary only. Keep private JSON on the server. No extraction or database writes occurred.')
        return 0 if ready else 1
    except (OSError, ValueError, tarfile.TarError, EOFError, zlib.error) as error:
        # Exception messages may include source names; expose only known limit codes.
        code = str(error) if isinstance(error, ValueError) and re.fullmatch('[a-z_]+', str(error)) else type(error).__name__
        print('VALIDATION STOPPED: %s. Input files are unchanged.' % code)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
