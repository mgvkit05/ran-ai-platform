#!/usr/bin/env python3
"""Offline ZIP/CSV intake. Python 3.9+, standard library only.

Run with $HOME/ran-ai/venv/bin/python 03_process_landing.py
Collector contract: upload as NAME.zip.part, close, then rename to NAME.zip.
Only completed, top-level ZIPs are read. Archives are retained, never extracted.
CSV metadata stays in the local SQLite database; no CSV data is printed.
This phase validates and inventories CSVs; it does not load KPI values into ClickHouse.
"""
import argparse
import csv
from contextlib import closing
import hashlib
import io
import json
import logging
import os
from pathlib import Path, PurePosixPath
import sqlite3
import stat
import sys
from datetime import datetime, timezone
import zipfile

MAX_MEMBERS = 10000
MAX_BYTES = 2 * 1024 ** 3
MAX_MEMBER_BYTES = 512 * 1024 ** 2


def signature(file_stat):
    return (file_stat.st_dev, file_stat.st_ino, file_stat.st_size, file_stat.st_mtime_ns)


def inspect_archive(stream, encoding):
    """Return deterministic metadata; reject unsafe names and malformed CSVs."""
    results = []
    with zipfile.ZipFile(stream) as archive:
        members = archive.infolist()
        if len(members) > MAX_MEMBERS:
            raise ValueError("archive member limit exceeded")
        if sum(item.file_size for item in members) > MAX_BYTES:
            raise ValueError("archive uncompressed size limit exceeded")
        seen = set()
        for item in members:
            name = item.orig_filename
            path = PurePosixPath(name)
            if (not name or "\\" in name or path.is_absolute()
                    or ".." in path.parts or ":" in name or "\x00" in name):
                raise ValueError("unsafe archive path")
            normalized = str(path)
            if normalized in seen:
                raise ValueError("duplicate archive path")
            seen.add(normalized)
            mode = item.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError("archive symlink is unsupported")
            if item.flag_bits & 1:
                raise ValueError("encrypted archive member is unsupported")
            if item.file_size > MAX_MEMBER_BYTES:
                raise ValueError("archive member size limit exceeded")
            if item.is_dir():
                continue
            if path.suffix.lower() != ".csv":
                # Validate CRCs even for auxiliary non-CSV members.
                with archive.open(item) as source:
                    while source.read(1024 * 1024):
                        pass
                continue
            with archive.open(item) as source:
                with io.TextIOWrapper(source, encoding=encoding, newline="") as text:
                    reader = csv.reader(text, strict=True)
                    header = next(reader, None)
                    if not header or any(not column.strip() for column in header):
                        raise ValueError("CSV header is empty or contains unnamed columns")
                    if len(set(header)) != len(header):
                        raise ValueError("CSV header has duplicate columns")
                    rows = 0
                    for row in reader:
                        if not row:
                            continue
                        if len(row) != len(header):
                            raise ValueError("CSV row width differs from header")
                        rows += 1
            results.append({"member": normalized, "columns": header, "rows": rows})
    if not results:
        raise ValueError("archive contains no CSV files")
    return results


def process(root, encoding="utf-8-sig"):
    landing = root / "data" / "landing"
    database_dir = root / "database" / "sqlite"
    if not landing.is_dir() or not database_dir.is_dir():
        raise ValueError("project directories missing; run Phase 2 first")
    totals = {"accepted": 0, "duplicates": 0, "failed": 0, "csv_files": 0, "rows": 0}
    # Fail fast on a concurrent run; database writes are transactional.
    import fcntl
    with (database_dir / "intake.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("another intake run is active") from None
        with closing(sqlite3.connect(str(database_dir / "intake.sqlite3"))) as database:
            database.execute("""CREATE TABLE IF NOT EXISTS archives (
                sha256 TEXT PRIMARY KEY, metadata_json TEXT NOT NULL,
                accepted_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            )""")
            database.commit()
            for path in sorted(landing.iterdir()):
                if path.is_symlink() or not path.is_file() or path.suffix.lower() != ".zip":
                    continue
                try:
                    with path.open("rb") as stream:
                        before = signature(os.fstat(stream.fileno()))
                        digest = hashlib.sha256()
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(chunk)
                        key = digest.hexdigest()
                        if database.execute("SELECT 1 FROM archives WHERE sha256=?", (key,)).fetchone():
                            totals["duplicates"] += 1
                            continue
                        stream.seek(0)
                        metadata = inspect_archive(stream, encoding)
                        if (signature(os.fstat(stream.fileno())) != before
                                or signature(path.stat()) != before):
                            raise ValueError("archive changed during intake")
                    with database:
                        database.execute("INSERT INTO archives (sha256, metadata_json) VALUES (?,?)",
                                         (key, json.dumps(metadata)))
                    totals["accepted"] += 1
                    totals["csv_files"] += len(metadata)
                    totals["rows"] += sum(item["rows"] for item in metadata)
                except (OSError, ValueError, UnicodeError, csv.Error, zipfile.BadZipFile,
                        RuntimeError, NotImplementedError) as error:
                    totals["failed"] += 1
                    # Avoid printing source filenames or proprietary CSV content.
                    logging.warning("Archive rejected (%s); original retained in landing.", type(error).__name__)
    return totals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.home() / "ran-ai")
    parser.add_argument("--encoding", default="utf-8-sig")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        log_path = args.root / "logs" / ("03-intake-%s-%d.log" %
                    (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), os.getpid()))
        logging.basicConfig(level=logging.INFO, format="%(message)s", handlers=[
            logging.FileHandler(str(log_path), encoding="utf-8"), logging.StreamHandler(sys.stdout)])
        totals = process(args.root, args.encoding)
    except (OSError, ValueError, sqlite3.Error) as error:
        logging.error("Intake could not start (%s). Check project directories, permissions and active runs.", type(error).__name__)
        return 1
    logging.info("=== ZIP intake result ===")
    for label, count in totals.items():
        logging.info("%s: %d", label, count)
    logging.info("Original ZIPs retained. No services started or network connections made.")
    logging.info("Timestamped log saved under the project logs directory.")
    return 1 if totals["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
