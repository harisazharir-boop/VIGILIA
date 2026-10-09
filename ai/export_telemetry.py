"""Export redacted complete telemetry rows from SQLite for offline training."""
from __future__ import annotations
import argparse, json, sqlite3
from pathlib import Path
from ai.anomaly import export_rows

parser = argparse.ArgumentParser(); parser.add_argument("database", type=Path); parser.add_argument("output", type=Path); args = parser.parse_args()
connection = sqlite3.connect(args.database)
rows = [json.loads(row[0]) for row in connection.execute('SELECT sample FROM Telemetry')]
args.output.write_text(json.dumps({"format": "vigilia-telemetry-dataset", "version": 1, "rows": export_rows(rows)}, separators=(",", ":")), encoding="utf-8")
