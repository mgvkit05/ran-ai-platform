#!/usr/bin/env python3
"""Display non-zero counter values directly from ClickHouse; no LLM is called.

Save on the AI server as /home/sysadmin/ran-ai/app/19_view_nonzero_counters.py.
Run from any directory, without sudo:
  /home/sysadmin/ran-ai/venv/bin/python /home/sysadmin/ran-ai/app/19_view_nonzero_counters.py
Optional examples:
  /home/sysadmin/ran-ai/venv/bin/python /home/sysadmin/ran-ai/app/19_view_nonzero_counters.py --rows 25
  /home/sysadmin/ran-ai/venv/bin/python /home/sysadmin/ran-ai/app/19_view_nonzero_counters.py --counter "throughput"
  /home/sysadmin/ran-ai/venv/bin/python /home/sysadmin/ran-ai/app/19_view_nonzero_counters.py --ne-id "AUPF_104900110"
Arguments: --rows 1-1000 (default 10); --counter TEXT (case-insensitive name
substring); --ne-id TEXT (exact ID); -h/--help. Filters may be combined.
Uses only the standard library, local HTTP 127.0.0.1:18123, and existing private
ClickHouse credentials. No database writes, report files, or model requests.
The non-zero test uses finite Float64 conversion; original values are printed
unchanged. Results are a limited selection, not necessarily the latest/largest.
Keep real data output private on the AI server.
"""
import argparse
import json
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


def query(rows=10, counter=None, ne_id=None):
    if not 1 <= rows <= 1000:
        raise ValueError('rows must be between 1 and 1000')
    conditions = ['isFinite(toFloat64OrNull(value_raw))', 'toFloat64OrNull(value_raw) != 0']
    parameters = {'limit': rows}
    if counter is not None:
        if not counter.strip() or len(counter) > 128:
            raise ValueError('counter must contain 1-128 characters')
        conditions.append('positionCaseInsensitiveUTF8(counter_name, {counter:String}) > 0')
        parameters['counter'] = counter
    if ne_id is not None:
        conditions.append('r.ne_id={ne_id:String}')
        parameters['ne_id'] = ne_id
    sql = '''SELECT r.init_time_utc, r.ne_id, r.location, f.family, f.version,
      f.counter_names[counter_position] AS counter_name,
      r.raw_values[7 + counter_position] AS value_raw
      FROM ran_ai.counter_records AS r
      INNER JOIN ran_ai.counter_files AS f
        ON r.load_id=f.load_id AND r.member_ordinal=f.member_ordinal
      ARRAY JOIN arrayEnumerate(f.counter_names) AS counter_position
      WHERE %s
      LIMIT {limit:UInt32} FORMAT JSON''' % ' AND '.join(conditions)
    return sql, parameters


def fetch(credentials, sql, parameters):
    arguments = {'query': sql}
    arguments.update({'param_' + key: str(value) for key, value in parameters.items()})
    # HTTP GET enforces read-only mode. Do not attempt to change readonly again.
    request = urllib.request.Request('http://127.0.0.1:18123/?' + urllib.parse.urlencode(arguments),
                headers={'X-ClickHouse-User': credentials['user'], 'X-ClickHouse-Key': credentials['password']},
                method='GET')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=70) as response:
            return json.load(response)['data']
    except urllib.error.HTTPError as error:
        body = error.read(8192).decode(errors='replace')
        code = re.search(r'\bCode:\s*(\d+)', body)
        names = re.findall(r'\(([A-Z][A-Z0-9_]{2,})\)', body)
        raise RuntimeError('ClickHouse HTTP %d%s%s' %
            (error.code, '; Code ' + code[1] if code else '', ' (' + names[-1] + ')' if names else '')) from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rows', type=int, default=10)
    parser.add_argument('--counter', help='case-insensitive counter-name substring')
    parser.add_argument('--ne-id', help='exact NE ID')
    args = parser.parse_args()
    try:
        sql, parameters = query(args.rows, args.counter, args.ne_id)
        path = Path.home() / 'ran-ai/config/clickhouse-server/credentials.json'
        credentials = json.loads(path.read_text())
        print('=== Non-zero ClickHouse counters (no LLM) ===', flush=True)
        print('Querying committed data; numeric filter uses Float64, output retains original strings.', flush=True)
        records = fetch(credentials, sql, parameters)
        for number, record in enumerate(records, 1):
            print('[%d] %s' % (number, json.dumps(record, ensure_ascii=True)))
        print('Displayed: %d non-zero counter value(s); requested maximum: %d.' % (len(records), args.rows))
        if not records:
            print('No finite non-zero numeric values matched the filters in committed data.')
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, urllib.error.URLError) as error:
        message = str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__
        print('QUERY FAILED: %s. Check local ClickHouse availability and ingestion.' % message, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
