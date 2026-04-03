"""Diagnose analyze runtime/import failures and process state."""
from __future__ import annotations

import importlib
import inspect
from pathlib import Path

from musesleuth.db import get_connection


def main() -> None:
    conn = get_connection("music.db")

    failed = conn.execute(
        """
        SELECT count(*) AS c
        FROM jobs
        WHERE stage = 'analyze' AND status = 'failed'
        """
    ).fetchone()["c"]
    running = conn.execute(
        """
        SELECT count(*) AS c
        FROM jobs
        WHERE stage = 'analyze' AND status = 'running'
        """
    ).fetchone()["c"]
    sample = conn.execute(
        """
        SELECT metadata_id, last_error
        FROM jobs
        WHERE stage = 'analyze' AND status = 'failed'
        ORDER BY id
        LIMIT 5
        """
    ).fetchall()

    bpm = importlib.import_module("musesleuth.bpm_analyzer")
    analyze = importlib.import_module("musesleuth.analyze_runner")

    print("=== Runtime module inspection ===")
    print(f"bpm_analyzer path: {Path(inspect.getsourcefile(bpm) or '')}")
    print(f"analyze_runner path: {Path(inspect.getsourcefile(analyze) or '')}")
    print(f"has analyze_energy: {hasattr(bpm, 'analyze_energy')}")
    print(f"has EnergyResult: {hasattr(bpm, 'EnergyResult')}")
    print()

    print("=== Job state ===")
    print(f"analyze failed: {failed}")
    print(f"analyze running: {running}")
    print()

    print("=== Sample errors ===")
    for row in sample:
        print(f"{row['metadata_id']}: {row['last_error']}")

    conn.close()


if __name__ == "__main__":
    main()
