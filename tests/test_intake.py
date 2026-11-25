import importlib.util
from contextlib import closing
import io
from pathlib import Path
import sqlite3
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location("intake", Path(__file__).parents[1] / "scripts" / "03_process_landing.py")
intake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(intake)


def archive(name="counters.csv", content="cell,value\nA,12\nB,15\n"):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as target:
        info = zipfile.ZipInfo("placeholder")
        info.filename = name
        target.writestr(info, content)
    stream.seek(0)
    return stream


class IntakeTests(unittest.TestCase):
    def test_csv_counts_quoted_values_and_bom(self):
        result = intake.inspect_archive(archive(content='\ufeffcell,value\n"A,B",12\n'), "utf-8-sig")
        self.assertEqual(result, [{"member": "counters.csv", "columns": ["cell", "value"], "rows": 1}])

    def test_unsafe_path_rejected(self):
        for name in ("../escape.csv", "/absolute.csv", "folder\\escape.csv"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                intake.inspect_archive(archive(name), "utf-8-sig")

    def test_malformed_width_and_headers_rejected(self):
        for content in ("a,b\n1\n", "a,a\n1,2\n", "a,\n1,2\n", ""):
            with self.subTest(content=content), self.assertRaises(ValueError):
                intake.inspect_archive(archive(content=content), "utf-8-sig")

    def test_size_limit(self):
        old_limit = intake.MAX_MEMBER_BYTES
        try:
            intake.MAX_MEMBER_BYTES = 1
            with self.assertRaises(ValueError):
                intake.inspect_archive(archive(), "utf-8-sig")
        finally:
            intake.MAX_MEMBER_BYTES = old_limit

    def test_duplicates_incomplete_uploads_and_rejected_retry(self):
        # Exercise intake transactions on Windows too; OS flock is verified on Linux.
        fake_lock = types.SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=lambda *args: None)
        with patch.dict("sys.modules", {"fcntl": fake_lock}), tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            landing = root / "data" / "landing"
            landing.mkdir(parents=True)
            (root / "database" / "sqlite").mkdir(parents=True)
            good = archive().getvalue()
            (landing / "one.zip").write_bytes(good)
            (landing / "copy.zip").write_bytes(good)
            (landing / "upload.zip.part").write_bytes(good)
            (landing / "bad.zip").write_bytes(b"incomplete")
            totals = intake.process(root)
            self.assertEqual(totals, {"accepted": 1, "duplicates": 1, "failed": 1, "csv_files": 1, "rows": 2})
            (landing / "bad.zip").write_bytes(archive(content="cell,value\nC,20\n").getvalue())
            second = intake.process(root)
            self.assertEqual(second["accepted"], 1)
            self.assertEqual(second["duplicates"], 2)
            with closing(sqlite3.connect(str(root / "database" / "sqlite" / "intake.sqlite3"))) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM archives").fetchone()[0], 2)
            self.assertTrue((landing / "one.zip").exists())

    def test_concurrent_run_rejected(self):
        def occupied(*args):
            raise BlockingIOError()
        fake_lock = types.SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=occupied)
        with patch.dict("sys.modules", {"fcntl": fake_lock}), tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "data" / "landing").mkdir(parents=True)
            (root / "database" / "sqlite").mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, "another intake run"):
                intake.process(root)


if __name__ == "__main__":
    unittest.main()
