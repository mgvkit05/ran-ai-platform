#!/usr/bin/env python3
"""List affected CSV filenames from the existing private validation report.
Run on the AI server; displays local filenames but never data row values.
"""
import argparse
import json
from pathlib import Path


def affected_files(report):
    return [item for item in report['csvs']
            if item.get('index_record_status') == 'blank'
            and item.get('empty_common_fields', {}).get('LOCATION', 0) > 0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offset', type=int, default=0, help='number of affected files to skip')
    parser.add_argument('--limit', type=int, default=5, help='number of affected files to display')
    args = parser.parse_args()
    if args.offset < 0 or args.limit < 1:
        parser.error('offset must be nonnegative and limit must be positive')
    reports = Path.home() / 'ran-ai' / 'reports'
    try:
        candidates = list(reports.glob('12-validation-*-private.json'))
        if not candidates:
            raise ValueError('No full-row validation report found. Run the latest Phase 12 script first.')
        report_path = max(candidates, key=lambda path: path.stat().st_mtime_ns)
        report = json.loads(report_path.read_text(encoding='utf-8'))
        if any('index_record_status' not in item for item in report['csvs']):
            raise ValueError('This report predates the index diagnostics. Rerun the latest Phase 12 script.')
        files = affected_files(report)
        print('Files with blank #index and empty LOCATION: %d' % len(files))
        print('Affected LOCATION rows: %d' % sum(item['empty_common_fields']['LOCATION'] for item in files))
        print('Displaying filenames from the LOCAL private report; no row values follow.')
        for position, item in enumerate(files[args.offset:args.offset + args.limit], args.offset + 1):
            print('\n[%d] %s' % (position, item['member']))
            print('    rows=%d; empty LOCATION rows=%d; index=blank' %
                  (item['rows'], item['empty_common_fields']['LOCATION']))
        next_offset = args.offset + args.limit
        if next_offset < len(files):
            print('\nFor the next page, append: --offset %d --limit %d' % (next_offset, args.limit))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print('FAILED: %s' % error)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
