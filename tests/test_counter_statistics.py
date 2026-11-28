import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('stat_tools', Path(__file__).parents[1] / 'scripts/16_counter_tools.py')
tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tools)


def args(**changes):
    values = dict(action='stats', counter='Example', ne_type='AUPF', family=None, data_version=None,
                  schema_id=None, question='Show statistics', rows=5, location=None, ne_id=None,
                  from_utc=None, to_utc=None, analysis='stats')
    values.update(changes)
    return argparse.Namespace(**values)


ITEM = dict(counter_label='Example(count)', family='Family', version='1', unit_label='count',
            schema_id='a' * 64, counter_ordinal=1)
ROW = dict(raw_value='14', ne_id='AUPF_1', system_id='01', ne_name='Private', location='',
           init_time_utc='2026-11-26 20:15:00', gran_period_raw='15')
SUMMARY = dict(matching_rows='100', numeric_rows='98', empty_rows='1', other_rows='1',
               zero_rows='10', minimum=-1, maximum=14, average=5, first_utc='2026-11-26 20:15:00',
               last_utc='2026-11-26 20:30:00', utc_unavailable='0')


class StatisticsTests(unittest.TestCase):
    def test_server_self_test_uses_read_only_literal_fixtures(self):
        fixture_summary = dict(matching_rows='7', numeric_rows='4', empty_rows='1', other_rows='2',
                               zero_rows='1', minimum=-1, maximum=10, average=3, utc_unavailable='7')
        with patch.object(tools, 'db', side_effect=[[fixture_summary], [{'raw_value': '1e1'}],
             [{'raw_value': '-1'}], [{'matching_rows': '0', 'minimum': None, 'maximum': None, 'average': None}]]) as query, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(tools.test_statistics({}), 0)
            for call in query.call_args_list:
                self.assertIn('FROM numbers(7)', call.args[0])
                self.assertNotIn('ran_ai.', call.args[0])
                self.assertNotIn('INSERT', call.args[0])

    def test_server_self_test_fails_on_incorrect_aggregate_results(self):
        with patch.object(tools, 'db', return_value=[dict(SUMMARY)]):
            with self.assertRaises(RuntimeError):
                tools.test_statistics({})

    def test_stats_cli_routes_and_keeps_time_flags(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'ran-ai'
            config = root / 'config/clickhouse-server'
            config.mkdir(parents=True)
            (config / 'credentials.json').write_text('{}')
            with patch.object(tools.Path, 'home', return_value=Path(temporary)), \
                 patch('sys.argv', ['16_counter_tools.py', 'stats', '--counter', 'Example', '--ne-type', 'AUPF',
                                    '--from', '2026-11-26', '--to', '2026-11-27']), \
                 patch.object(tools, 'ask_counter', return_value=0) as ask:
                self.assertEqual(tools.main(), 0)
                self.assertEqual(ask.call_args.args[0].action, 'stats')
                self.assertEqual(ask.call_args.args[0].analysis, 'stats')
                self.assertEqual(ask.call_args.args[0].to_utc, '2026-11-27')

    def test_time_bounds_normalize_offsets_and_reject_reversed_intervals(self):
        filters = tools.query_filters(args(from_utc='2026-11-26T15:15:00-05:00', to_utc='2026-11-26T20:30:00Z'))
        self.assertEqual(filters['from_utc'], '2026-11-26 20:15:00.000000')
        self.assertEqual(filters['to_utc'], '2026-11-26 20:30:00.000000')
        for start, end in [('bad', None), ('2026-11-27', '2026-11-26'), ('2026-11-26', '2026-11-26')]:
            with self.assertRaises(ValueError):
                tools.query_filters(args(from_utc=start, to_utc=end))

    def test_aggregate_is_unlimited_and_bound_to_all_explicit_filters(self):
        filters = tools.query_filters(args(ne_id="x' OR 1=1 --", location='', from_utc='2026-11-26', to_utc='2026-11-27'))
        sql = tools.statistics_sql(filters)
        self.assertNotIn('LIMIT', sql)
        self.assertNotIn("x' OR 1=1", sql)
        for placeholder in ('{schema:String}', '{family:String}', '{version:String}', '{column:UInt32}',
                            '{ne_pattern:String}', '{ne_id:String}', '{location:String}',
                            "{from_utc:DateTime64(6, 'UTC')}", "{to_utc:DateTime64(6, 'UTC')}"):
            self.assertIn(placeholder, sql)
        self.assertIn('init_time_utc>=', sql)
        self.assertIn('init_time_utc<', sql)
        for function in ('minOrNull', 'maxOrNull', 'avgOrNull', 'isFinite', 'isNull(numeric_value)'):
            self.assertIn(function, sql)
        self.assertEqual(filters['location'], '')

    def test_rankings_share_aggregate_filters_and_have_stable_tie_order(self):
        filters = {'ne_id': 'AUPF_1'}
        high = tools.ranked_sql(filters, 'highest')
        low = tools.ranked_sql(filters, 'lowest')
        self.assertIn('numeric_value DESC', high)
        self.assertIn('numeric_value ASC', low)
        for sql in (high, low):
            self.assertIn('WHERE isNotNull(numeric_value)', sql)
            self.assertIn('archive_sha256,member_ordinal,row_ordinal', sql)
            self.assertIn('{ne_id:String}', sql)
            self.assertIn('LIMIT {limit:UInt32}', sql)
        with self.assertRaises(ValueError):
            tools.ranked_sql({}, 'DROP TABLE')

    def test_stats_covers_more_than_display_limit_and_never_calls_ollama(self):
        with patch.object(tools, 'db', side_effect=[[ITEM], [dict(SUMMARY)], [ROW], [ROW]]) as query, \
             patch.object(tools, 'explain') as explain, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(tools.ask_counter(args(rows=1, ne_id='AUPF_1', location=''), Path('.'), {}), 0)
        self.assertIn('100', output.getvalue())
        self.assertIn('[S1]', output.getvalue())
        self.assertEqual(len(query.call_args_list), 4)
        self.assertNotIn('LIMIT', query.call_args_list[1].args[0])
        self.assertEqual(query.call_args_list[1].args[1]['ne_id'], 'AUPF_1')
        explain.assert_not_called()

    def test_ask_highest_passes_full_statistics_and_ranking_to_model(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(tools, 'db', side_effect=[[ITEM], [dict(SUMMARY)], [ROW]]) as query, \
             patch.object(tools, 'add_documentation'), patch.object(tools, 'explain') as explain, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(tools.ask_counter(args(action='ask', analysis='auto', question='Show the highest value'), Path(temporary), {}), 0)
            evidence = explain.call_args.args[1]
            self.assertEqual(evidence['statistics']['matching_rows'], '100')
            self.assertEqual(evidence['analysis'], 'highest')
            self.assertIn('numeric_value DESC', query.call_args_list[2].args[0])
            self.assertIn('full-data statistics', tools.compact_evidence(evidence)['scope'])

    def test_no_rows_or_no_numeric_values_do_not_invent_extrema(self):
        for matching in ('0', '4'):
            summary = dict(SUMMARY, matching_rows=matching, numeric_rows='0', minimum=None, maximum=None, average=None)
            with patch.object(tools, 'db', side_effect=[[ITEM], [summary]]) as query, \
                 patch.object(tools, 'explain') as explain, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(tools.ask_counter(args(), Path('.'), {}), 0)
                self.assertEqual(len(query.call_args_list), 2)
                explain.assert_not_called()

    def test_auto_intents_and_explicit_override(self):
        for question, mode in [('What is the average?', 'stats'), ('lowest value', 'lowest'),
                               ('highest value', 'highest'), ('What does this measure?', 'sample')]:
            self.assertEqual(tools.analysis_mode(args(analysis='auto', question=question)), mode)
        self.assertEqual(tools.analysis_mode(args(analysis='sample', question='highest')), 'sample')

    def test_sample_mode_also_uses_time_and_ne_id_filters(self):
        with patch.object(tools, 'db', side_effect=[[ITEM], [ROW]]) as query, \
             patch.object(tools, 'add_documentation'), patch.object(tools, 'explain'), \
             contextlib.redirect_stdout(io.StringIO()):
            tools.ask_counter(args(action='ask', analysis='sample', ne_id='AUPF_1', from_utc='2026-11-26'), Path('.'), {})
            self.assertIn('{ne_id:String}', query.call_args_list[1].args[0])
            self.assertIn("{from_utc:DateTime64(6, 'UTC')}", query.call_args_list[1].args[0])


if __name__ == '__main__':
    unittest.main()
