#!/usr/bin/env python3
"""View ClickHouse data locally in the AI server's SSH terminal (read-only).

Save as: /home/sysadmin/ran-ai/app/15_view_sample_data.py
Run from any directory, without sudo:
  "$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/15_view_sample_data.py"
Optional: append --rows 5 --counters 5, or --member 100 to select a CSV member.
No laptop HTTP connection, browser, extra packages or firewall changes needed.
Output contains real lab data. Keep it local; no report file is written.
"""
import argparse
import json
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request


def sample_sql(rows, counters, member=None):
    if not 1 <= rows <= 20 or not 1 <= counters <= 20:
        raise ValueError('rows and counters must each be between 1 and 20')
    if member is not None and not 1 <= member <= 10000:
        raise ValueError('member must be between 1 and 10000')
    condition = '' if member is None else 'WHERE member_ordinal = %d' % member
    return '''SELECT r.member_ordinal, r.row_ordinal, f.member_name, f.family,
      f.version, f.index_present, f.index_definition, r.ne_id, r.system_id,
      r.ne_name, r.init_time_raw, r.time_offset_raw, r.gran_period_raw,
      r.init_time_utc, r.location,
      arraySlice(f.counter_names,1,%d) AS counter_labels,
      arraySlice(r.raw_values,8,%d) AS counter_values,
      length(f.counter_names) AS total_counters
    FROM (SELECT * FROM ran_ai.counter_records %s
          ORDER BY archive_sha256,member_ordinal,row_ordinal LIMIT %d) AS r
    INNER JOIN ran_ai.counter_files AS f
      ON r.load_id=f.load_id AND r.member_ordinal=f.member_ordinal
    ORDER BY r.member_ordinal,r.row_ordinal FORMAT JSON''' % (counters, counters, condition, rows)


def fetch(opener, credentials, sql):
    # Fixed SELECT queries only. Explicit read-only mode, local endpoint, no proxy.
    parameters = urllib.parse.urlencode({'query': sql, 'readonly': '1'})
    request = urllib.request.Request('http://127.0.0.1:18123/?' + parameters,
                headers={'X-ClickHouse-User': credentials['user'],
                         'X-ClickHouse-Key': credentials['password']}, method='GET')
    try:
        with opener.open(request, timeout=70) as response:
            return json.load(response)['data']
    except urllib.error.HTTPError as error:
        # An error body can contain source values; do not dump it automatically.
        raise RuntimeError('ClickHouse returned HTTP %d; confirm the database and ingestion views are available.' % error.code) from None


def display(value):
    # Quote strings, including empty strings; escape terminal control characters.
    return json.dumps(value, ensure_ascii=True)


def render(records):
    if not records:
        print('No committed records matched this selection.')
        return
    for position, record in enumerate(records, 1):
        print('\n=== Sample %d: CSV member %s, source row %s ===' %
              (position, record['member_ordinal'], record['row_ordinal']))
        for label, key in (('Source CSV', 'member_name'), ('Family', 'family'), ('Version', 'version'),
                           ('NE_ID', 'ne_id'), ('SYSTEM_ID', 'system_id'), ('NE_NAME', 'ne_name'),
                           ('INIT_TIME (original)', 'init_time_raw'), ('TIME_OFFSET (original)', 'time_offset_raw'),
                           ('UTC timestamp', 'init_time_utc'), ('GRAN_PERIOD (raw)', 'gran_period_raw'),
                           ('LOCATION', 'location')):
            print('%s: %s' % (label, display(record.get(key))))
        state = 'absent' if not record['index_present'] else 'present'
        print('Index record: %s; definition: %s' % (state, display(record['index_definition'])))
        print('Counter samples (%d of %s; original string representations):' %
              (len(record['counter_labels']), record['total_counters']))
        for name, value in zip(record['counter_labels'], record['counter_values']):
            print('  %s = %s' % (display(name), display(value)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rows', type=int, default=3)
    parser.add_argument('--counters', type=int, default=3)
    parser.add_argument('--member', type=int, help='CSV member ordinal from the original archive')
    args = parser.parse_args()
    try:
        sql = sample_sql(args.rows, args.counters, args.member)
        root = Path.home() / 'ran-ai'
        credentials = json.loads((root / 'config/clickhouse-server/credentials.json').read_text())
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        overview = fetch(opener, credentials, '''SELECT count() AS source_rows,
                 min(init_time_utc) AS first_utc, max(init_time_utc) AS last_utc
                 FROM ran_ai.counter_records FORMAT JSON''')[0]
        files = fetch(opener, credentials, 'SELECT count() AS csv_files FROM ran_ai.counter_files FORMAT JSON')[0]
        print('=== Committed ClickHouse data ===')
        print('CSV files: %s; source rows: %s' % (files['csv_files'], overview['source_rows']))
        print('UTC coverage: %s to %s' % (display(overview['first_utc']), display(overview['last_utc'])))
        print('Displaying real lab data locally. No database writes or report files.')
        render(fetch(opener, credentials, sql))
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, urllib.error.URLError) as error:
        if isinstance(error, RuntimeError):
            print(str(error), file=sys.stderr)
        else:
            print('VIEW FAILED (%s). Confirm ClickHouse is running and TAR ingestion succeeded.' % type(error).__name__, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
