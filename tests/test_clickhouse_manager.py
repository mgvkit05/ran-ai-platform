import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

spec = importlib.util.spec_from_file_location('clickhouse_manager', Path(__file__).parents[1] / 'scripts' / '10_manage_clickhouse.py')
manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manager)


class ClickHouseManagerTests(unittest.TestCase):
    def test_config_is_local_authenticated_and_resource_limited(self):
        server, users = manager.settings(Path('/home/example/ran & ai'), 'private-secret', 'other-secret')
        config = ET.fromstring(server)
        accounts = ET.fromstring(users)
        self.assertEqual(config.findtext('listen_host'), '127.0.0.1')
        self.assertEqual(config.findtext('http_port'), '18123')
        self.assertIsNone(config.find('tcp_port'))
        self.assertEqual(int(config.findtext('max_server_memory_usage')), 64 * 1024 ** 3)
        self.assertEqual(accounts.findtext('profiles/ran_ai/max_threads'), '8')
        self.assertNotIn('private-secret', users)
        self.assertEqual(accounts.findtext('users/ran_ai/networks/ip'), '127.0.0.1')
        self.assertIn('ran & ai', config.findtext('path'))

    def test_credentials_are_reused_not_rotated_on_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'config').mkdir()
            path, credentials = manager.configure(root)
            original = path.read_text()
            second_path, second_credentials = manager.configure(root)
            self.assertEqual(path, second_path)
            self.assertEqual(credentials, second_credentials)
            self.assertEqual(path.read_text(), original)

    def test_synthetic_kpi_and_cleanup(self):
        commands = []
        def execute(credentials, sql):
            commands.append(sql)
            return json.dumps({'data': [{'rows': '2', 'successes': '193', 'attempts': '200', 'success_rate': 96.5}]}) if sql.startswith('SELECT') else ''
        self.assertEqual(manager.smoke_test({}, execute)['success_rate'], 96.5)
        table = commands[1].split()[2]
        self.assertTrue(table.startswith('ran_ai.setup_test_'))
        self.assertEqual(commands[-1], 'DROP TABLE IF EXISTS ' + table)

    def test_failed_insert_still_cleans_only_its_own_table(self):
        commands = []
        def execute(credentials, sql):
            commands.append(sql)
            if sql.startswith('INSERT'):
                raise RuntimeError('synthetic insert failed')
            return ''
        with self.assertRaisesRegex(RuntimeError, 'synthetic insert failed'):
            manager.smoke_test({}, execute)
        self.assertEqual(commands[-1], 'DROP TABLE IF EXISTS ' + commands[1].split()[2])

    def test_stop_does_not_signal_unknown_process(self):
        with patch.object(manager, 'owned', return_value=False), patch.object(manager.os, 'killpg', create=True) as signal_group:
            with self.assertRaisesRegex(RuntimeError, 'no process signalled'):
                manager.terminate({'pid': 123})
            signal_group.assert_not_called()


if __name__ == '__main__':
    unittest.main()
