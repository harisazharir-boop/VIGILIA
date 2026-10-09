"""Consistent SQLite backup/restore helper. No production path defaults."""
from __future__ import annotations
import argparse
import sqlite3
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('source', type=Path)
parser.add_argument('target', type=Path)
args = parser.parse_args()
if not args.source.is_file(): raise SystemExit('source database missing')
args.target.parent.mkdir(parents=True, exist_ok=True)
source = sqlite3.connect(f'file:{args.source}?mode=ro', uri=True)
target = sqlite3.connect(args.target)
try: source.backup(target)
finally: target.close(); source.close()
