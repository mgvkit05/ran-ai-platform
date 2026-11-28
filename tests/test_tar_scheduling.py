import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path, PurePosixPath
import tempfile
import types
import unittest
from unittest.mock import patch

from test_tar_batches import local_remote

spec = importlib.util.spec_from_file_location('scheduled_transfer', Path(__file__).parents[1] / 'scripts/18_transfer_tar_batches.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def arguments(root, **changes):
    values = dict(action='install-schedule', role='collector', config=root / 'config.json',
                  spool=root / 'spool', inbox=root / 'ran-ai/data/landing/server-batches')
    values.update(changes)
    return argparse.Namespace(**values)


class TarSchedulingTests(unittest.TestCase):
    def test_remote_setup_invokes_ai_script_with_ingest_role_and_inbox(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / 'AI scheduler.py'
            script.write_text('import json,sys\nprint(json.dumps(sys.argv[1:]))\n')
            response = json.loads(local_remote({'directory': str(root)},
                dict(operation='schedule', script=str(script), action='install-schedule'), 30))
            self.assertEqual(response['returncode'], 0)
            self.assertEqual(json.loads(response['output']), ['install-schedule', '--role', 'ingest', '--inbox', str(root)])

    def test_status_reports_both_missing_and_installed_jobs_without_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = arguments(Path(temporary), action='schedule-status')
            for table, result in [('', 1), ('# BEGIN RAN-AI-TAR collector\njob\n# END RAN-AI-TAR collector\n', 0)]:
                with patch.object(bridge, 'cron_active', return_value=True), \
                     patch.object(bridge, 'read_crontab', return_value=table), \
                     patch.object(bridge, 'cron_command') as write, contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(bridge.manage_schedule(args, Path(temporary)), result)
                    write.assert_not_called()

    def test_managed_cron_update_is_idempotent_and_preserves_other_jobs(self):
        existing = 'MAILTO=""\n0 2 * * * /usr/bin/backup\n'
        script = Path('/home/user/ran-ai-transfer/18_transfer_tar_batches.py')
        entry = '*/15 * * * * /bin/sh /home/user/run/collector.sh'
        updated = bridge.replace_schedule(existing, 'collector', entry, script)
        self.assertEqual(updated, bridge.replace_schedule(updated, 'collector', entry, script))
        self.assertIn('0 2 * * * /usr/bin/backup\n', updated)
        self.assertEqual(updated.count('# BEGIN RAN-AI-TAR collector'), 1)
        self.assertEqual(bridge.replace_schedule(updated, 'collector', None, script), existing)

    def test_earlier_manual_entry_for_same_script_is_replaced(self):
        script = PurePosixPath('/home/user/18_transfer_tar_batches.py')
        old = '*/15 * * * * /usr/bin/python3 %s run >> /home/user/log 2>&1\n' % script
        other = '* * * * * /usr/bin/python3 /other/18_transfer_tar_batches.py run\n'
        updated = bridge.replace_schedule(old + other, 'collector', '*/15 * * * * /bin/sh /new/job', script)
        self.assertNotIn(str(script) + ' run', updated)
        self.assertIn(other, updated)

    def test_malformed_managed_block_stops_without_overwriting_cron(self):
        with self.assertRaises(ValueError):
            bridge.replace_schedule('# BEGIN RAN-AI-TAR collector\njob\n', 'collector', 'new', Path('/script'))

    def test_installer_writes_correct_interval_and_keeps_config_secrets_out(self):
        for role, interval, action in (('collector', '*/15 * * * *', 'run'), ('ingest', '* * * * *', 'ingest')):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                ai_root = root / 'ran-ai'
                credentials = ai_root / 'config/clickhouse-server/credentials.json'
                credentials.parent.mkdir(parents=True)
                credentials.write_text('{}')
                state = {'cron': '0 2 * * * /backup\n'}
                writes = []
                def cron(command, text=None):
                    self.assertEqual(command, ['crontab', '-'])
                    state['cron'] = text
                    writes.append(text)
                    return types.SimpleNamespace(returncode=0)
                args = arguments(root, role=role)
                with patch.object(bridge, 'cron_active', return_value=True), \
                     patch.object(bridge, 'load_config', return_value={'password': 'SENTINEL_SECRET'}), \
                     patch.object(bridge, 'read_crontab', side_effect=lambda: state['cron']), \
                     patch.object(bridge, 'cron_command', cron), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(bridge.manage_schedule(args, ai_root), 0)
                    self.assertEqual(bridge.manage_schedule(args, ai_root), 0)
                self.assertEqual(len(writes), 1)
                self.assertIn(interval + ' /bin/sh ', state['cron'])
                self.assertIn('/backup', state['cron'])
                base = args.spool if role == 'collector' else ai_root
                launcher = (base / 'run' / ('scheduled-' + role + '.sh')).read_text()
                self.assertIn(' ' + action + ' ', launcher)
                self.assertIn('umask 077', launcher)
                self.assertNotIn('SENTINEL_SECRET', launcher + state['cron'])
                self.assertEqual(len(list((base / 'run').glob('crontab-before-*.txt'))), 1)

    def test_inactive_cron_prevents_installation(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(bridge, 'cron_active', return_value=False), patch.object(bridge, 'read_crontab') as read:
            with self.assertRaisesRegex(RuntimeError, 'cron_daemon_not_active'):
                bridge.manage_schedule(arguments(Path(temporary)), Path(temporary) / 'ran-ai')
            read.assert_not_called()

    def test_automation_preflights_sources_then_installs_ai_and_collector(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = dict(sources=[dict(id='one', host='producer', user='reader', directory='/log/pm/oss/raw/nr')],
                          ai=dict(host='ai', user='sysadmin', directory='/home/sysadmin/ran-ai/data/landing/server-batches'))
            requests = []
            def ssh(target, request, timeout):
                requests.append(request)
                return b'[]' if request['operation'] == 'plan' else json.dumps(dict(returncode=0, output='AI installed\n')).encode()
            with patch.object(bridge, 'load_config', return_value=config), \
                 patch.object(bridge, 'cron_active', return_value=True), patch.object(bridge, 'read_crontab', return_value=''), \
                 patch.object(bridge, 'ssh_call', ssh), patch.object(bridge, 'manage_schedule', return_value=0) as install, \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(bridge.automation(arguments(root, action='install-automation'), root), 0)
            self.assertEqual([r['operation'] for r in requests], ['plan', 'schedule'])
            self.assertEqual(requests[1]['action'], 'install-schedule')
            self.assertTrue(requests[1]['script'].replace('\\', '/').endswith('/app/18_transfer_tar_batches.py'))
            self.assertEqual(install.call_args.args[0].role, 'collector')

    def test_failed_ai_setup_does_not_enable_collector(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = dict(sources=[], ai=dict(directory='/home/sysadmin/ran-ai/data/landing/server-batches'))
            with patch.object(bridge, 'load_config', return_value=config), \
                 patch.object(bridge, 'cron_active', return_value=True), patch.object(bridge, 'read_crontab', return_value=''), \
                 patch.object(bridge, 'ssh_call', return_value=b'{"returncode":2,"output":"","error":"old script"}'), \
                 patch.object(bridge, 'manage_schedule') as install, contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, 'remote_schedule_failed'):
                    bridge.automation(arguments(root, action='install-automation'), root)
                install.assert_not_called()

    def test_unavailable_producer_does_not_block_automation_setup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = dict(sources=[dict(id='standby', host='producer', user='reader', directory='/logs')],
                          ai=dict(host='ai', user='sysadmin', directory='/home/sysadmin/ran-ai/data/landing/server-batches'))
            def ssh(target, request, timeout):
                if request['operation'] == 'plan':
                    raise RuntimeError('source temporarily offline')
                return b'{"returncode":0,"output":"AI installed"}'
            with patch.object(bridge, 'load_config', return_value=config), \
                 patch.object(bridge, 'cron_active', return_value=True), patch.object(bridge, 'read_crontab', return_value=''), \
                 patch.object(bridge, 'ssh_call', ssh), patch.object(bridge, 'manage_schedule', return_value=0), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(bridge.automation(arguments(root, action='install-automation'), root), 0)


if __name__ == '__main__':
    unittest.main()
