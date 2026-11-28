import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_tar_batches import bridge, setup, local_remote, make_archive, ProvenanceDatabase, loader_for


def send(root):
    config, spool, ai_root, inbox = setup(root)
    for number, source in enumerate(config['sources'], 1):
        make_archive(Path(source['directory']) / 'same.tar', rows=number)
    with patch.object(bridge, 'ssh_call', local_remote), contextlib.redirect_stdout(io.StringIO()):
        bridge.transfer(config, spool, io.StringIO())
    return config, spool, ai_root, inbox


class AIArchiveCleanupTests(unittest.TestCase):
    def test_ingested_archives_are_deleted_with_audit_and_metadata_retained(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, _, ai_root, inbox = send(Path(temporary))
            db = ProvenanceDatabase()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(bridge.ingest_batches(inbox, ai_root, io.StringIO(), loader_for(db)), 0)
            self.assertEqual(len(db.visible_rows()), 6)
            self.assertEqual(list(inbox.glob('*/*/*.tar')), [])
            folder = next(inbox.glob('*/.ready')).parent
            for name in ('manifest.json', '.ready', '.ingested', '.cleaned'):
                self.assertTrue((folder / name).exists())
            events = [json.loads(line) for line in (ai_root / 'logs/ingest-deletions.jsonl').read_text().splitlines()]
            deleted = [event for event in events if event['result'] == 'deleted']
            self.assertEqual(len(deleted), 2)
            self.assertTrue(all(event['reason'] == 'clickhouse_commit_verified' for event in deleted))
            for source in config['sources']:
                self.assertTrue((Path(source['directory']) / 'same.tar').exists())

    def test_existing_ingested_batches_are_cleaned_without_reloading_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, _, ai_root, inbox = send(Path(temporary))
            db = ProvenanceDatabase()
            with patch.object(bridge, 'cleanup_ingested_batch', return_value=(0, 0)), contextlib.redirect_stdout(io.StringIO()):
                bridge.ingest_batches(inbox, ai_root, io.StringIO(), loader_for(db))
            self.assertEqual(len(list(inbox.glob('*/*/*.tar'))), 2)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(bridge.ingest_batches(inbox, ai_root, io.StringIO(), loader_for(db)), 0)
            self.assertEqual(list(inbox.glob('*/*/*.tar')), [])
            self.assertEqual(len(db.visible_rows()), 6)

    def test_failed_ingestion_retains_received_archives(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, _, ai_root, inbox = send(Path(temporary))
            db = ProvenanceDatabase()
            db.bad_counts = True
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(bridge.ingest_batches(inbox, ai_root, io.StringIO(), loader_for(db)), 1)
            self.assertEqual(len(list(inbox.glob('*/*/*.tar'))), 2)
            self.assertEqual(db.visible_rows(), [])
            self.assertEqual(list(inbox.glob('*/.cleaned')), [])

    def test_ingested_marker_without_database_commit_does_not_allow_deletion(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, _, ai_root, inbox = send(Path(temporary))
            folder = next(inbox.glob('*/.ready')).parent
            bridge.save_json(folder / '.ingested', {'completed_utc': 'fixture'})
            db = ProvenanceDatabase()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(bridge.ingest_batches(inbox, ai_root, io.StringIO(), loader_for(db)), 1)
            self.assertEqual(len(list(inbox.glob('*/*/*.tar'))), 2)

    def test_partial_cleanup_retries_without_reingesting(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, _, ai_root, inbox = send(Path(temporary))
            db = ProvenanceDatabase()
            unlink = Path.unlink
            def busy(path, *args, **kwargs):
                if path.suffix == '.tar' and path.parent.name == 'source-1':
                    raise PermissionError('simulated busy archive')
                return unlink(path, *args, **kwargs)
            with patch.object(Path, 'unlink', busy), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(bridge.ingest_batches(inbox, ai_root, io.StringIO(), loader_for(db)), 1)
            self.assertEqual(len(list(inbox.glob('*/*/*.tar'))), 1)
            self.assertEqual(len(db.visible_rows()), 6)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(bridge.ingest_batches(inbox, ai_root, io.StringIO(), loader_for(db)), 0)
            self.assertEqual(list(inbox.glob('*/*/*.tar')), [])
            self.assertEqual(len(db.visible_rows()), 6)

    def test_unknown_delivery_confirmation_can_retry_after_AI_deleted_archives(self):
        with tempfile.TemporaryDirectory() as temporary:
            config, spool, ai_root, inbox = setup(Path(temporary))
            make_archive(Path(config['sources'][0]['directory']) / 'same.tar')
            def disconnect_after_publish(target, request, timeout, **kwargs):
                result = local_remote(target, request, timeout, **kwargs)
                if request['operation'] == 'publish':
                    raise RuntimeError('connection closed before sender saw confirmation')
                return result
            with patch.object(bridge, 'ssh_call', disconnect_after_publish), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(RuntimeError):
                    bridge.transfer(config, spool, io.StringIO())
            self.assertEqual(len(list(spool.glob('cache/*/*.tar'))), 1)
            db = ProvenanceDatabase()
            with contextlib.redirect_stdout(io.StringIO()):
                bridge.ingest_batches(inbox, ai_root, io.StringIO(), loader_for(db))
            self.assertEqual(list(inbox.glob('*/*/*.tar')), [])
            with patch.object(bridge, 'ssh_call', local_remote), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(bridge.transfer(config, spool, io.StringIO()), 0)
            self.assertEqual(list(spool.glob('cache/*/*.tar')), [])
            self.assertEqual(list(inbox.glob('*/*/*.tar')), [])
            self.assertEqual(len(db.visible_rows()), 2)


if __name__ == '__main__':
    unittest.main()
