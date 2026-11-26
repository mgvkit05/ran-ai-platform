import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('clickhouse_install', Path(__file__).parents[1] / 'scripts' / '09_install_clickhouse_offline.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class ClickHouseInstallerTests(unittest.TestCase):
    def test_only_binary_is_copied_and_install_scripts_not_extracted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / 'package.tgz'
            with tarfile.open(package, 'w:gz') as archive:
                for name in ('../../escape', 'clickhouse-common-static-26.9.1.1/install/doinst.sh',
                             'clickhouse-common-static-26.9.1.1/usr/bin/clickhouse'):
                    item = tarfile.TarInfo(name)
                    item.size = 4
                    archive.addfile(item, io.BytesIO(b'test'))
            installer.unpack_binary(package, root / 'binary')
            self.assertEqual((root / 'binary').read_bytes(), b'test')
            self.assertEqual(sorted(path.name for path in root.iterdir()), ['binary', 'package.tgz'])

    def test_symlink_binary_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / 'package.tgz'
            with tarfile.open(package, 'w:gz') as archive:
                item = tarfile.TarInfo('clickhouse-common-static-26.9.1.1/usr/bin/clickhouse')
                item.type = tarfile.SYMTYPE
                item.linkname = '/outside'
                archive.addfile(item)
            with self.assertRaises(ValueError):
                installer.unpack_binary(package, root / 'binary')
            self.assertFalse((root / 'binary').exists())


if __name__ == '__main__':
    unittest.main()
