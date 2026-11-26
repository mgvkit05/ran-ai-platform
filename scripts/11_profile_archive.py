#!/usr/bin/env python3
"""Profile a completed TAR/ZIP in landing without extraction or exposing row values.
Run: "$HOME/ran-ai/venv/bin/python" 11_profile_archive.py
Defaults to the newest supported archive. Original files are never modified.
"""
import argparse
import csv
from datetime import datetime, timezone
import gzip
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import tarfile
import zipfile

SAMPLE_BYTES = 1024 * 1024
TOTAL_SAMPLE_BYTES = 64 * 1024 * 1024
MAX_ROWS = 200
MAX_MEMBERS = 10000


def supported(path):
    return path.name.lower().endswith(('.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tar.xz', '.zip'))


def safe_name(name):
    path = PurePosixPath(name)
    return not (path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name)


def role(header):
    lower = header.lower()
    if any(word in lower for word in ('timestamp', 'datetime', 'start_time', 'end_time', 'date', 'period')):
        return 'time_or_period'
    if any(word in lower for word in ('cell', 'gnb', 'enodeb', 'object', 'distname', 'dn')):
        return 'entity_candidate'
    if any(word in lower for word in ('counter', 'measurement', 'metric')):
        return 'counter_candidate'
    return 'unclassified'


def describe(content):
    truncated = len(content) > SAMPLE_BYTES
    content = content[:SAMPLE_BYTES]
    try:
        text = content.decode('utf-8-sig')
        encoding = 'utf-8-sig'
    except UnicodeDecodeError:
        if content.startswith((b'\xff\xfe', b'\xfe\xff')):
            text = content.decode('utf-16', errors='replace')
            encoding = 'utf-16'
        else:
            text = content.decode('latin-1')
            encoding = 'latin-1-fallback'
    # Vendor CSVs may contain preamble records. Find a likely wide header within
    # the first 20 records; this is a hypothesis to verify, not a schema decision.
    choices = []
    for delimiter in (',', ';', '\t', '|'):
        rows = []
        reader = csv.reader(io.StringIO(text, newline=''), delimiter=delimiter, strict=True)
        try:
            for index, row in enumerate(reader):
                if index >= 20:
                    break
                rows.append(row)
        except csv.Error:
            pass
        if rows:
            widest = max(range(len(rows)), key=lambda index: len(rows[index]))
            choices.append((len(rows[widest]), delimiter, widest, rows[widest]))
    if not choices:
        raise ValueError('no readable CSV records')
    width, delimiter, header_index, headers = max(choices, key=lambda choice: choice[0])
    numeric = [0] * width
    nonempty = [0] * width
    observed = 0
    mismatched = 0
    parse_error = False
    reader = csv.reader(io.StringIO(text, newline=''), delimiter=delimiter, strict=True)
    try:
        for index, row in enumerate(reader):
            if index <= header_index or not row:
                continue
            if observed >= MAX_ROWS:
                break
            observed += 1
            if len(row) != width:
                mismatched += 1
                continue
            for column, value in enumerate(row):
                value = value.strip()
                if value:
                    nonempty[column] += 1
                    if re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', value):
                        numeric[column] += 1
    except csv.Error:
        parse_error = True
    columns = [{'position': i + 1, 'header': header, 'role_hint': role(header),
                'sample_type': ('empty' if nonempty[i] == 0 else
                                'numeric' if numeric[i] == nonempty[i] else 'text_or_mixed')}
               for i, header in enumerate(headers)]
    return {'delimiter': delimiter, 'encoding': encoding, 'header_record_candidate': header_index + 1,
            'columns': columns, 'sampled_rows': observed, 'width_mismatches': mismatched,
            'sample_byte_limit_reached': truncated, 'sample_parse_incomplete': parse_error,
            'blank_header_count': sum(not header.strip() for header in headers),
            'duplicate_header_count': len(headers) - len(set(headers))}


def profile(path):
    result = {'archive_type': 'zip' if zipfile.is_zipfile(path) else 'tar',
              'members_seen': 0, 'csv_members_seen': 0, 'csv_profiled': [],
              'unsafe_or_link_members_skipped': 0, 'member_profile_errors': 0,
              'sample_budget_reached': False}
    sampled_bytes = 0
    before = path.stat()

    def consume(name, incoming):
        nonlocal sampled_bytes
        if sampled_bytes >= TOTAL_SAMPLE_BYTES:
            result['sample_budget_reached'] = True
            return
        try:
            amount = min(SAMPLE_BYTES + 1, TOTAL_SAMPLE_BYTES - sampled_bytes)
            if name.lower().endswith('.gz'):
                with gzip.GzipFile(fileobj=incoming) as decompressed:
                    content = decompressed.read(amount)
            else:
                content = incoming.read(amount)
            sampled_bytes += len(content)
            detail = describe(content)
            detail['member'] = name  # Local private report only.
            result['csv_profiled'].append(detail)
        except (ValueError, OSError, EOFError, csv.Error):
            result['member_profile_errors'] += 1

    if result['archive_type'] == 'zip':
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                result['members_seen'] += 1
                if result['members_seen'] > MAX_MEMBERS:
                    raise ValueError('archive member limit exceeded')
                if not safe_name(member.filename) or ((member.external_attr >> 16) & 0o170000) == 0o120000:
                    result['unsafe_or_link_members_skipped'] += 1
                    continue
                if not member.is_dir() and member.filename.lower().endswith(('.csv', '.csv.gz')):
                    result['csv_members_seen'] += 1
                    if member.flag_bits & 1:
                        result['member_profile_errors'] += 1
                        continue
                    with archive.open(member) as incoming:
                        consume(member.filename, incoming)
    else:
        with tarfile.open(path, 'r|*') as archive:
            for member in archive:
                result['members_seen'] += 1
                if result['members_seen'] > MAX_MEMBERS:
                    raise ValueError('archive member limit exceeded')
                if not safe_name(member.name) or member.issym() or member.islnk():
                    result['unsafe_or_link_members_skipped'] += 1
                    continue
                if member.isfile() and member.name.lower().endswith(('.csv', '.csv.gz')):
                    result['csv_members_seen'] += 1
                    with archive.extractfile(member) as incoming:
                        consume(member.name, incoming)
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('archive changed while profiling; wait for upload completion')
    return result


def sanitized(result, max_schemas=None):
    lines = ['=== Archive structure (no filenames, headers or row values) ===',
             'Format: %s; members: %d; CSV members: %d; CSVs profiled: %d' %
             (result['archive_type'], result['members_seen'], result['csv_members_seen'], len(result['csv_profiled']))]
    groups = {}
    for item in result['csv_profiled']:
        key = (item['delimiter'], item['encoding'], item['header_record_candidate'], tuple(column['header'] for column in item['columns']))
        groups.setdefault(key, []).append(item)
    lines.append('Distinct candidate schemas: %d' % len(groups))
    for index, items in enumerate(groups.values(), 1):
        if max_schemas is not None and index > max_schemas:
            lines.append('Additional schemas omitted from terminal: %d. Full sanitized summary is in the report file.' % (len(groups) - max_schemas))
            break
        first = items[0]
        columns = first['columns']
        delimiter = {'\t': 'TAB', ',': 'COMMA', ';': 'SEMICOLON', '|': 'PIPE'}[first['delimiter']]
        lines.append('Schema-%03d: files=%d columns=%d delimiter=%s encoding=%s candidate_header_record=%d' %
                     (index, len(items), len(columns), delimiter, first['encoding'], first['header_record_candidate']))
        lines.append('  sampled_rows=%d width_mismatches=%d blank_headers=%d duplicate_headers=%d' %
                     (sum(item['sampled_rows'] for item in items), sum(item['width_mismatches'] for item in items),
                      first['blank_header_count'], first['duplicate_header_count']))
        hints = [str(column['position']) + ':' + column['role_hint'] for column in columns if column['role_hint'] != 'unclassified']
        lines.append('  role hints (column positions): ' + (', '.join(hints[:12]) or 'none'))
        lines.append('  numeric columns in first sampled file: %d; incomplete samples: %d' %
                     (sum(column['sample_type'] == 'numeric' for column in columns),
                      sum(item['sample_parse_incomplete'] for item in items)))
    lines.append('Skipped unsafe/link members: %d; profile errors: %d; sample budget reached: %s' %
                 (result['unsafe_or_link_members_skipped'], result['member_profile_errors'], result['sample_budget_reached']))
    lines.append('Header locations and role hints are provisional. Samples do not establish full-file validity or total row counts.')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', nargs='?', type=Path)
    parser.add_argument('--max-schemas', type=int, default=8, help='maximum schemas printed; full summary is always saved')
    args = parser.parse_args()
    os.umask(0o077)
    root = Path.home() / 'ran-ai'
    try:
        if args.max_schemas < 1:
            raise ValueError('max-schemas must be positive')
        landing = (root / 'data' / 'landing').resolve()
        candidates = [item for item in landing.iterdir() if not item.is_symlink() and item.is_file() and supported(item)]
        if args.archive is None:
            if not candidates:
                raise ValueError('no completed TAR/ZIP archive found in landing')
            archive = max(candidates, key=lambda item: item.stat().st_mtime_ns)
        else:
            archive = args.archive if args.archive.is_absolute() else landing / args.archive
        if archive.is_symlink():
            raise ValueError('archive symlinks are unsupported')
        archive.resolve().relative_to(landing)
        result = profile(archive)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-%d' % os.getpid()
        private = root / 'reports' / ('11-archive-private-' + stamp + '.json')
        shared = root / 'reports' / ('11-archive-summary-' + stamp + '.txt')
        with private.open('x', encoding='utf-8') as output:
            json.dump(result, output, indent=2, ensure_ascii=True)
        summary = sanitized(result)
        with shared.open('x', encoding='utf-8') as output:
            output.write(summary + '\n')
        print(sanitized(result, args.max_schemas))
        print('Share only the summary text above (or its summary report). Keep the private JSON on the lab server.')
        print('Reports saved under $HOME/ran-ai/reports. No archive extraction or database writes performed.')
        return 0 if result['csv_profiled'] else 1
    except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as error:
        print('FAILED: %s. Check the archive type and upload completion; no input files changed.' % type(error).__name__)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
