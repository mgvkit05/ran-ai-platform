import contextlib
import ast
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from test_vendor_ingest import FakeDatabase, make_archive, loader as vendor

spec = importlib.util.spec_from_file_location('tar_batches', Path(__file__).parents[1] / 'scripts/18_transfer_tar_batches.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def local_remote(target, request, timeout, incoming=None, outgoing=None):
    # Execute the actual remote program locally: no SSH/network or fake copy logic.
    payload = json.dumps(dict(request, directory=target['directory'])).encode() + b'\n'
    if incoming is not None:
        payload += incoming.read()
    result = subprocess.run([sys.executable, '-c', bridge.REMOTE], input=payload,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace'))
    if outgoing is not None:
        outgoing.write(result.stdout)
    return result.stdout


def setup(root):
    ai_root = root / 'ran-ai'
    inbox = ai_root / 'data/landing/server-batches'
    inbox.mkdir(parents=True)
    (ai_root / 'config/clickhouse-server').mkdir(parents=True)
    (ai_root / 'config/clickhouse-server/credentials.json').write_text('{}')
    sources = []
    for number in (1, 2):
        directory = root / ('producer%d' % number)
        directory.mkdir()
        sources.append(dict(id='source-%d' % number, host='host%d' % number, user='reader',
                            directory=str(directory), min_age_seconds=0, patterns=['*.tar']))
    config = dict(sources=sources, ai=dict(host='ai', user='sysadmin', directory=str(inbox)), timeout_seconds=30)
    return config, root / 'spool', ai_root, inbox


class ProvenanceDatabase(FakeDatabase):
    def __init__(self):
        super().__init__()
        self.tables[bridge.PROVENANCE] = []

    def execute(self, sql, body=''):
        if sql.startswith('SELECT count() FROM ' + bridge.PROVENANCE):
            identifier = sql.split("origin_id='")[1].split("'")[0]
            return str(sum(r['origin_id'] == identifier for r in self.tables[bridge.PROVENANCE]))
        return super().execute(sql, body)


def loader_for(db):
    return types.SimpleNamespace(Database=lambda credentials: db, initialize=vendor.initialize,
                                 Batch=vendor.Batch, ingest=vendor.ingest)


class TarBatchTests(unittest.TestCase):
    def test_successful_delivery_deletes_only_intermediate_tar_and_audits_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, spool, _, inbox = setup(Path(temporary))
            producer = Path(config['sources'][0]['directory']) / 'same.tar'
            make_archive(producer)
            with patch.object(bridge, 'ssh_call', local_remote), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(bridge.transfer(config, spool, io.StringIO()), 0)
            self.assertTrue(producer.exists())
            self.assertEqual(list(spool.glob('cache/*/*.tar')), [])
            self.assertEqual(len(list(inbox.glob('*/*/*.tar'))), 1)
            events = [json.loads(line) for line in (spool / 'logs/deletions.jsonl').read_text().splitlines()]
            self.assertEqual([r['result'] for r in events], ['delete_intent', 'deleted'])
            self.assertTrue(events[1]['original_file'].endswith('same.tar'))
            self.assertEqual(events[1]['reason'], 'verified_AI_delivery')
            self.assertIn('utc', events[1])

    def test_failed_delivery_confirmation_keeps_intermediate_copies(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, spool, _, _ = setup(Path(temporary))
            make_archive(Path(config['sources'][0]['directory']) / 'same.tar')
            def no_confirmation(target, request, timeout, **kwargs):
                if request['operation'] == 'publish':
                    return b'Unexpected output'
                return local_remote(target, request, timeout, **kwargs)
            with patch.object(bridge, 'ssh_call', no_confirmation), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, 'AI_delivery_confirmation_not_received'):
                    bridge.transfer(config, spool, io.StringIO())
            self.assertEqual(len(list(spool.glob('cache/*/*.tar'))), 1)
            self.assertFalse((spool / 'logs/deletions.jsonl').exists())

    def test_failed_deletion_retries_without_resending_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, spool, _, inbox = setup(Path(temporary))
            make_archive(Path(config['sources'][0]['directory']) / 'same.tar')
            unlink = Path.unlink
            def refuse_tar(path, *args, **kwargs):
                if path.suffix == '.tar':
                    raise PermissionError('simulated file busy')
                return unlink(path, *args, **kwargs)
            with patch.object(bridge, 'ssh_call', local_remote), patch.object(Path, 'unlink', refuse_tar), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(bridge.transfer(config, spool, io.StringIO()), 1)
            self.assertEqual(len(list(spool.glob('cache/*/*.tar'))), 1)
            with patch.object(bridge, 'ssh_call', local_remote), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(bridge.transfer(config, spool, io.StringIO()), 0)
            self.assertEqual(list(spool.glob('cache/*/*.tar')), [])
            self.assertEqual(len(list(inbox.glob('*/.ready'))), 1)

    def test_cleanup_crash_recovery_does_not_try_to_resend_deleted_tar(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, spool, _, inbox = setup(Path(temporary))
            make_archive(Path(config['sources'][0]['directory']) / 'same.tar')
            with patch.object(bridge, 'ssh_call', local_remote), contextlib.redirect_stdout(io.StringIO()):
                bridge.transfer(config, spool, io.StringIO())
                sent = json.loads(next((spool / 'sent').glob('*.json')).read_text())
                bridge.save_json(spool / 'active-batch.json', sent)
                self.assertEqual(bridge.transfer(config, spool, io.StringIO()), 0)
            self.assertFalse((spool / 'active-batch.json').exists())
            self.assertEqual(len(list(inbox.glob('*/.ready'))), 1)

    def test_pending_origin_prevents_deletion_of_shared_cache_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, spool, _, _ = setup(Path(temporary))
            source = config['sources'][0]
            producer = Path(source['directory']) / 'same.tar'
            make_archive(producer)
            with patch.object(bridge, 'ssh_call', local_remote):
                bridge.collect_source(source, spool, 30)
            item = json.loads(next((spool / 'records').glob('*.json')).read_text())
            batch = dict(version=1, batch_id='confirmed', sources=[source['id']], archives=[item])
            bridge.save_json(spool / 'sent/confirmed.json', batch)
            pending = dict(item, origin_id='a' * 64, source_path='/other/original.tar')
            bridge.save_json(spool / 'records' / ('a' * 64 + '.json'), pending)
            with contextlib.redirect_stdout(io.StringIO()):
                bridge.cleanup_delivered(spool, {item['origin_id']}, io.StringIO())
            self.assertEqual(len(list(spool.glob('cache/*/*.tar'))), 1)
            self.assertFalse((spool / 'cleaned/confirmed.json').exists())

    def test_changed_delivered_cache_file_is_never_deleted(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, spool, _, _ = setup(Path(temporary))
            source = config['sources'][0]
            make_archive(Path(source['directory']) / 'same.tar')
            with patch.object(bridge, 'ssh_call', local_remote):
                bridge.collect_source(source, spool, 30)
            item = json.loads(next((spool / 'records').glob('*.json')).read_text())
            batch = dict(version=1, batch_id='confirmed', sources=[source['id']], archives=[item])
            bridge.save_json(spool / 'sent/confirmed.json', batch)
            cache = next(spool.glob('cache/*/*.tar'))
            cache.write_bytes(b'changed file')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(bridge.cleanup_delivered(spool, {item['origin_id']}, io.StringIO()))
            self.assertTrue(cache.exists())
            event = json.loads((spool / 'logs/deletions.jsonl').read_text().splitlines()[-1])
            self.assertEqual(event['result'], 'delete_failed')

    def test_only_latest_tar_is_collected_then_waits_without_falling_back(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, spool, _, _ = setup(Path(temporary))
            source = config['sources'][0]
            directory = Path(source['directory'])
            for number in (1, 2, 3):
                path = directory / ('period%d.tar' % number)
                make_archive(path, rows=number)
                os.utime(path, ns=(1000000000 * number, 1000000000 * number))
            with patch.object(bridge, 'ssh_call', local_remote):
                self.assertEqual(bridge.collect_source(source, spool, 30), 1)
                records = [json.loads(p.read_text()) for p in spool.glob('records/*.json')]
                self.assertEqual(len(records), 1)
                self.assertTrue(records[0]['source_path'].endswith('period3.tar'))
                self.assertEqual(bridge.collect_source(source, spool, 30), 0)
                (directory / 'period3.tar').unlink()
                self.assertEqual(bridge.collect_source(source, spool, 30), 0)
                fresh = directory / 'period4.tar'
                make_archive(fresh, rows=4)
                os.utime(fresh, ns=(4000000000, 4000000000))
                self.assertEqual(bridge.collect_source(source, spool, 30), 1)
            self.assertEqual(len(list(spool.glob('cache/source-1/*.tar'))), 2)

    def test_failed_download_does_not_advance_latest_cursor(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, spool, _, _ = setup(Path(temporary))
            source = config['sources'][0]
            make_archive(Path(source['directory']) / 'newest.tar')
            def failed(target, request, timeout, **kwargs):
                if request['operation'] == 'fetch':
                    raise RuntimeError('interrupted download')
                return local_remote(target, request, timeout, **kwargs)
            with patch.object(bridge, 'ssh_call', failed):
                with self.assertRaises(RuntimeError):
                    bridge.collect_source(source, spool, 30)
            self.assertFalse((spool / 'cursors/source-1.json').exists())
            with patch.object(bridge, 'ssh_call', local_remote):
                self.assertEqual(bridge.collect_source(source, spool, 30), 1)

    def test_newest_selection_hashes_only_selected_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for number in (1, 2):
                path = directory / ('file%d.tar' % number)
                path.write_bytes(b'fixture')
                os.utime(path, ns=(number * 1000000000, number * 1000000000))
            program = bridge.REMOTE.replace('def checksum(p):',
                "def checksum(p):\n    if p.name!='file2.tar': raise AssertionError('old archive hashed')")
            request = dict(operation='list', directory=str(directory), patterns=['*.tar'], min_age_seconds=0, max_archive_bytes=1000)
            result = subprocess.run([sys.executable, '-c', program], input=json.dumps(request).encode() + b'\n',
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
            self.assertEqual([r['name'] for r in json.loads(result.stdout)], ['file2.tar'])

    def test_plan_only_lists_metadata_and_never_hashes_archive_contents(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            (path / 'sample.tar').write_bytes(b'fixture')
            request = dict(operation='plan', directory=str(path), patterns=['*.tar'],
                           min_age_seconds=0, max_archive_bytes=1000)
            # Fail if the real remote program attempts to call its checksum reader.
            program = bridge.REMOTE.replace('def checksum(p):',
                                            "def checksum(p):\n    raise AssertionError('plan must not read archive contents')")
            result = subprocess.run([sys.executable, '-c', program], input=json.dumps(request).encode() + b'\n',
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
            files = json.loads(result.stdout)
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0]['name'], 'sample.tar')
            self.assertEqual(files[0]['size'], 7)
            self.assertIsNone(files[0]['sha256'])

    def test_failure_diagnostics_report_json_location_without_config_values(self):
        output = io.StringIO()
        try:
            json.loads('{"password":"PRIVATE_SECRET", "sources":[,]}')
        except json.JSONDecodeError as error:
            with contextlib.redirect_stdout(output):
                bridge.report_failure(error, 'read-config')
        self.assertIn('code=CONFIG_JSON', output.getvalue())
        self.assertIn('line 1, column', output.getvalue())
        self.assertNotIn('PRIVATE_SECRET', output.getvalue())

    def test_failure_diagnostics_classify_ssh_failures_without_echoing_details(self):
        for message, code in [('Host key verification failed. PRIVATE_DETAIL', 'SSH_HOST_KEY'),
                              ('Permission denied (publickey,password). PRIVATE_DETAIL', 'SSH_AUTH'),
                              ('No route to host PRIVATE_DETAIL', 'SSH_CONNECTION'),
                              ('python3: command not found PRIVATE_DETAIL', 'REMOTE_PYTHON')]:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                bridge.report_failure(RuntimeError(message), 'plan-source-1')
            self.assertIn('code=' + code, output.getvalue())
            self.assertNotIn('PRIVATE_DETAIL', output.getvalue())

    def test_remote_non_json_output_is_not_reported_as_a_config_error(self):
        output = io.StringIO()
        try:
            json.loads('PRIVATE_REMOTE_BANNER')
        except json.JSONDecodeError as error:
            with contextlib.redirect_stdout(output):
                bridge.report_failure(error, 'plan-source-1')
        self.assertIn('REMOTE_OUTPUT', output.getvalue())
        self.assertNotIn('CONFIG_JSON', output.getvalue())
        self.assertNotIn('PRIVATE_REMOTE_BANNER', output.getvalue())

    def test_intermediate_and_remote_program_syntax_supports_python36(self):
        ast.parse(Path(bridge.__file__).read_text(), feature_version=(3, 6))
        ast.parse(bridge.REMOTE, feature_version=(3, 6))

    def test_one_failed_source_does_not_block_delivery_of_other_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, spool, _, inbox = setup(Path(temporary))
            for source in config['sources']:
                make_archive(Path(source['directory']) / 'same.tar')
            def broken(target, request, timeout, **kwargs):
                if target['host'] == 'host1':
                    raise RuntimeError('PRIVATE_CONNECTION_DETAIL')
                return local_remote(target, request, timeout, **kwargs)
            output = io.StringIO()
            log = io.StringIO()
            with patch.object(bridge, 'ssh_call', broken), contextlib.redirect_stdout(output):
                self.assertEqual(bridge.transfer(config, spool, log), 1)
            self.assertEqual(len(list(spool.glob('cache/source-2/*.tar'))), 0)
            self.assertEqual(len(list(inbox.glob('*/.ready'))), 1)
            self.assertNotIn('PRIVATE_CONNECTION_DETAIL', output.getvalue())
            self.assertIn('PRIVATE_CONNECTION_DETAIL', log.getvalue())

    def test_source_age_and_filename_pattern_leave_young_files_uncollected(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, spool, _, _ = setup(Path(temporary))
            source = dict(config['sources'][0], min_age_seconds=3600)
            make_archive(Path(source['directory']) / 'same.tar')
            with patch.object(bridge, 'ssh_call', local_remote):
                self.assertEqual(bridge.collect_source(source, spool, 30), 0)
            self.assertEqual(list(spool.glob('cache/*/*.tar')), [])

    def test_endpoint_rejects_shell_options_and_invalid_authentication(self):
        endpoint = dict(host='host.example', user='reader', directory='/log/pm/oss/raw/nr')
        bridge.endpoint(endpoint)
        for fields in (dict(host='-oProxyCommand=bad'), dict(user='reader;bad'),
                       dict(private_key='secret'), dict(python='python3; bad'), dict(password=''),
                       dict(password='line1\nline2'), dict(password='secret', identity_file='/key')):
            with self.assertRaises(ValueError):
                bridge.endpoint(dict(endpoint, **fields))

    def test_each_producer_password_is_scoped_to_its_own_child_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = dict(bridge.os.environ)
            for secret in ('producer-one-$quote"', 'producer-two-! spaced'):
                bridge.endpoint(dict(host='host', user='reader', directory='/logs', password=secret))
                with bridge.ssh_auth({'password': secret}, Path(temporary)) as (options, environment, detached):
                    helper = Path(environment['SSH_ASKPASS'])
                    self.assertTrue(helper.exists())
                    self.assertNotIn(secret, helper.read_text())
                    self.assertNotIn(secret, ' '.join(options))
                    self.assertEqual(environment['RAN_AI_SSH_PASSWORD'], secret)
                    self.assertIn('BatchMode=no', options)
                    self.assertTrue(detached)
                self.assertFalse(helper.exists())
            self.assertEqual(dict(bridge.os.environ), parent)

    def test_askpass_replies_only_to_password_prompts(self):
        environment = {'RAN_AI_ASKPASS_ACTIVE': '1', 'RAN_AI_SSH_PASSWORD': 'secret-$!'}
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(bridge.askpass("reader@host's password:", environment), 0)
        self.assertEqual(output.getvalue(), 'secret-$!\n')
        for prompt in ('Are you sure you want to continue connecting?', 'Verification code:', 'Enter passphrase for key:'):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(bridge.askpass(prompt, environment), 1)
            self.assertEqual(output.getvalue(), '')

    def test_key_authentication_remains_noninteractive_without_password_env(self):
        with patch.dict(bridge.os.environ, {'RAN_AI_SSH_PASSWORD': 'stale-secret', 'RAN_AI_ASKPASS_ACTIVE': '1'}):
            with bridge.ssh_auth({}) as (options, environment, detached):
                self.assertIn('BatchMode=yes', options)
                self.assertNotIn('RAN_AI_SSH_PASSWORD', environment)
                self.assertNotIn('RAN_AI_ASKPASS_ACTIVE', environment)
                self.assertFalse(detached)

    def test_password_config_requires_private_permissions_on_linux(self):
        from unittest.mock import Mock
        config = {'ai': {'host': 'ai', 'user': 'sysadmin', 'directory': '/inbox'},
                  'sources': [{'id': 'source-1', 'host': 'one', 'user': 'reader', 'directory': '/logs', 'password': 'secret'}]}
        path = Mock()
        path.read_text.return_value = json.dumps(config)
        with patch.object(bridge.sys, 'platform', 'linux'), patch.object(bridge.os, 'geteuid', return_value=1000, create=True):
            for owner, mode in ((1000, 0o100644), (2000, 0o100600)):
                path.stat.return_value = types.SimpleNamespace(st_uid=owner, st_mode=mode)
                with self.assertRaises(ValueError):
                    bridge.load_config(path)
            path.stat.return_value = types.SimpleNamespace(st_uid=1000, st_mode=0o100600)
            self.assertEqual(bridge.load_config(path)['sources'][0]['user'], 'reader')

    def test_password_never_enters_ssh_arguments_or_remote_request(self):
        captured = {}
        class Input(io.BytesIO):
            def close(self):
                captured['request'] = self.getvalue()
                super().close()
        class Process:
            def __init__(self, command, **kwargs):
                captured.update(command=command, kwargs=kwargs,
                                helper=Path(kwargs['env']['SSH_ASKPASS']).read_text())
                self.stdin = Input()
                self.stdout = io.BytesIO(b'[]')
                self.returncode = 0
            def poll(self):
                return 0
            def wait(self):
                return 0
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(bridge.Path, 'home', return_value=Path(temporary)), \
             patch.object(bridge.subprocess, 'Popen', Process):
            result = bridge.ssh_call({'host': 'host', 'user': 'reader', 'directory': '/logs', 'password': 'SENTINEL_SECRET'},
                                     {'operation': 'list'}, 30)
        self.assertEqual(result, b'[]')
        self.assertNotIn('SENTINEL_SECRET', ' '.join(captured['command']))
        self.assertNotIn(b'SENTINEL_SECRET', captured['request'])
        self.assertNotIn('SENTINEL_SECRET', captured['helper'])
        self.assertTrue(captured['kwargs']['start_new_session'])
        self.assertIn('StrictHostKeyChecking=yes', captured['command'])

    def test_same_filename_from_two_hosts_is_preserved_and_ingested(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, spool, ai_root, inbox = setup(Path(temporary))
            for source in config['sources']:
                source['password'] = 'PRIVATE_PRODUCER_PASSWORD'
            config['ai']['password'] = 'PRIVATE_AI_PASSWORD'
            make_archive(Path(config['sources'][0]['directory']) / 'same.tar', rows=1)
            make_archive(Path(config['sources'][1]['directory']) / 'same.tar', rows=2)
            with patch.object(bridge, 'ssh_call', local_remote), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(bridge.transfer(config, spool, io.StringIO()), 0)
            ready = list(inbox.glob('*/.ready'))
            self.assertEqual(len(ready), 1)
            batch = json.loads((ready[0].parent / 'manifest.json').read_text())
            self.assertEqual(len(batch['archives']), 2)
            self.assertEqual(len(list(spool.glob('cache/*/*.tar'))), 0)
            self.assertEqual(len(list(ready[0].parent.glob('*/*.tar'))), 2)
            for path in list(spool.rglob('*.json')) + list(inbox.rglob('*.json')):
                self.assertNotIn('PRIVATE_PRODUCER_PASSWORD', path.read_text())
                self.assertNotIn('PRIVATE_AI_PASSWORD', path.read_text())
            self.assertNotEqual(batch['archives'][0]['sha256'], batch['archives'][1]['sha256'])
            db = ProvenanceDatabase()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(bridge.ingest_batches(inbox, ai_root, io.StringIO(), loader_for(db)), 0)
            self.assertEqual(len(db.visible_rows()), 6)
            self.assertEqual(len(db.tables[bridge.PROVENANCE]), 2)
            self.assertTrue(all(r['source_path'].endswith('same.tar') for r in db.tables[bridge.PROVENANCE]))
            # Simulate loss of completion marker after database commits, then retry.
            (ready[0].parent / '.ingested').unlink()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(bridge.ingest_batches(inbox, ai_root, io.StringIO(), loader_for(db)), 0)
            self.assertEqual(len(db.visible_rows()), 6)
            self.assertEqual(len(db.tables[bridge.PROVENANCE]), 2)

    def test_pushes_available_files_without_waiting_and_does_not_resend(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, spool, _, inbox = setup(Path(temporary))
            first = Path(config['sources'][0]['directory']) / 'same.tar'
            make_archive(first)
            with patch.object(bridge, 'ssh_call', local_remote), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(bridge.transfer(config, spool, io.StringIO()), 0)
                self.assertEqual(len(list(inbox.glob('*/.ready'))), 1)
                make_archive(Path(config['sources'][1]['directory']) / 'same.tar')
                self.assertEqual(bridge.transfer(config, spool, io.StringIO()), 0)
                self.assertEqual(bridge.transfer(config, spool, io.StringIO()), 0)
            self.assertEqual(len(list(inbox.glob('*/.ready'))), 2)
            self.assertTrue(first.exists())

    def test_identical_tar_from_two_hosts_keeps_two_origins_and_one_data_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, spool, ai_root, inbox = setup(Path(temporary))
            first = Path(config['sources'][0]['directory']) / 'same.tar'
            make_archive(first)
            (Path(config['sources'][1]['directory']) / 'same.tar').write_bytes(first.read_bytes())
            with patch.object(bridge, 'ssh_call', local_remote), contextlib.redirect_stdout(io.StringIO()):
                bridge.transfer(config, spool, io.StringIO())
                db = ProvenanceDatabase()
                self.assertEqual(bridge.ingest_batches(inbox, ai_root, io.StringIO(), loader_for(db)), 0)
            self.assertEqual(len(db.visible_rows()), 2)
            self.assertEqual(len(db.tables[bridge.PROVENANCE]), 2)

    def test_source_reusing_a_filename_with_new_content_does_not_overwrite_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, spool, _, _ = setup(Path(temporary))
            source = config['sources'][0]
            path = Path(source['directory']) / 'same.tar'
            with patch.object(bridge, 'ssh_call', local_remote):
                make_archive(path, rows=1)
                bridge.collect_source(source, spool, 30)
                make_archive(path, rows=3)
                bridge.collect_source(source, spool, 30)
            self.assertEqual(len(list(spool.glob('cache/source-1/*.tar'))), 2)
            self.assertEqual(len(list(spool.glob('records/*.json'))), 2)

    def test_unpublished_or_corrupt_batch_never_starts_database_ingest(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, spool, ai_root, inbox = setup(Path(temporary))
            for source in config['sources']:
                make_archive(Path(source['directory']) / 'same.tar')
            with patch.object(bridge, 'ssh_call', local_remote), contextlib.redirect_stdout(io.StringIO()):
                bridge.transfer(config, spool, io.StringIO())
            ready = next(inbox.glob('*/.ready'))
            path = next(ready.parent.glob('*/*.tar'))
            path.write_bytes(b'corrupted')
            db = ProvenanceDatabase()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(bridge.ingest_batches(inbox, ai_root, io.StringIO(), loader_for(db)), 1)
            self.assertEqual(db.visible_rows(), [])
            self.assertFalse((ready.parent / '.ingested').exists())

    def test_failed_push_retries_same_batch_and_retains_queue(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, spool, _, inbox = setup(Path(temporary))
            for source in config['sources']:
                make_archive(Path(source['directory']) / 'same.tar')
            def fail_publish(target, request, timeout, **kwargs):
                if request['operation'] == 'publish':
                    raise RuntimeError('connection lost')
                return local_remote(target, request, timeout, **kwargs)
            with patch.object(bridge, 'ssh_call', fail_publish), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(RuntimeError):
                    bridge.transfer(config, spool, io.StringIO())
            self.assertTrue((spool / 'active-batch.json').exists())
            self.assertFalse((spool / 'delivered.json').exists())
            self.assertEqual(list(inbox.glob('*/.ready')), [])
            with patch.object(bridge, 'ssh_call', local_remote), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(bridge.transfer(config, spool, io.StringIO()), 0)
            self.assertFalse((spool / 'active-batch.json').exists())
            self.assertEqual(len(list(inbox.glob('*/.ready'))), 1)

    def test_manifest_missing_source_or_unsafe_id_is_rejected(self):
        for manifest in [dict(version=1, batch_id='../bad', sources=['one'], archives=[]),
                         dict(version=1, batch_id='good', sources=['one'], archives=[]),
                         dict(version=1, batch_id='good', sources=['one', 'two'], archives=[
                             dict(source_id='one', origin_id='a' * 64, sha256='b' * 64, size=1)])]:
            with self.assertRaises(ValueError):
                bridge.validate_manifest(manifest)


if __name__ == '__main__':
    unittest.main()
