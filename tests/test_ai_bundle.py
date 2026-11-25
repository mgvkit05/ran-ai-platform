from contextlib import nullcontext
import hashlib
import importlib.util
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import types
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('offline_ai', Path(__file__).parents[1] / 'scripts' / '05_install_ai_offline.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class BundleTests(unittest.TestCase):
    def test_relative_paths_confined(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(installer.within(root, 'bin/ollama'), root / 'bin' / 'ollama')
            for value in ('../escape', '/absolute', 'bin\\escape'):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    installer.within(root, value)

    def test_checksums_streamed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'blob'
            path.write_bytes(b'example model content')
            self.assertEqual(installer.sha256(path), hashlib.sha256(path.read_bytes()).hexdigest())

    def test_runtime_extract_and_reject_traversal(self):
        # Test TAR extraction independently of the platform-specific zstandard wheel.
        fake_zstd = types.SimpleNamespace(ZstdDecompressor=lambda: types.SimpleNamespace(stream_reader=lambda source: nullcontext(source)))
        with patch.dict(sys.modules, {'zstandard': fake_zstd}), tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, valid in (('bin/ollama', True), ('../escape', False)):
                archive = root / ('valid.tar' if valid else 'unsafe.tar')
                with tarfile.open(archive, 'w') as target:
                    entry = tarfile.TarInfo(name)
                    entry.size = 4
                    entry.mode = 0o755
                    target.addfile(entry, io.BytesIO(b'test'))
                destination = root / ('valid' if valid else 'unsafe')
                destination.mkdir()
                if valid:
                    installer.extract_runtime(archive, destination)
                    self.assertEqual((destination / name).read_bytes(), b'test')
                else:
                    with self.assertRaises(ValueError):
                        installer.extract_runtime(archive, destination)
                    self.assertFalse((root / 'escape').exists())

    def test_absolute_symlink_rejected(self):
        fake_zstd = types.SimpleNamespace(ZstdDecompressor=lambda: types.SimpleNamespace(stream_reader=lambda source: nullcontext(source)))
        with patch.dict(sys.modules, {'zstandard': fake_zstd}), tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'link.tar'
            with tarfile.open(archive, 'w') as target:
                entry = tarfile.TarInfo('lib/unsafe')
                entry.type = tarfile.SYMTYPE
                entry.linkname = '/outside'
                target.addfile(entry)
            with self.assertRaises(ValueError):
                installer.extract_runtime(archive, root / 'output')


if __name__ == '__main__':
    unittest.main()
