"""
SQLite tracking database — connection helper shared by the CLI and the
Prefect flow.

Both entry points wipe and recreate --output-dir on every run (see
renderer.render_report), but the tracking database (urgency scores, bullet
history, and future cross-run trackers) must survive across runs. Keeping
db_path management entirely separate from output_dir means a run can never
delete its own history by accident.
"""

import sqlite3
from pathlib import Path


def ensure_safe_db_path(db_path: Path, output_dir: Path) -> None:
    """
    Raise ValueError if db_path sits inside output_dir.

    output_dir is deleted and recreated by render_report() on every run. A
    db_path nested inside it (or equal to it) would be destroyed the moment
    the next run starts, silently erasing all accumulated history.
    """
    db_resolved = db_path.resolve()
    output_resolved = output_dir.resolve()
    if db_resolved == output_resolved or output_resolved in db_resolved.parents:
        raise ValueError(
            f"--db-path ({db_path}) is inside --output-dir ({output_dir}), "
            f"which is deleted and recreated on every run. Choose a --db-path "
            f"outside of --output-dir."
        )


def connect(db_path: Path) -> sqlite3.Connection:
    """
    Open the tracking database, creating it (and any missing parent
    directories) on first use. Never truncates or deletes an existing
    database file — that's what makes cross-run history possible.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(db_path)
