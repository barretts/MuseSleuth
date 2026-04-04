"""Chromaprint acoustic fingerprinting via fpcalc CLI."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class FingerprintResult:
    """Result from fingerprinting a single file."""

    fingerprint: Optional[str] = None
    duration: Optional[int] = None
    error: Optional[str] = None


def run_fpcalc(file_path: str) -> FingerprintResult:
    """Run fpcalc on a file and return the fingerprint.

    The actual fpcalc invocation is in _invoke_fpcalc so tests can mock it.
    """
    path = Path(file_path)
    if not path.exists():
        return FingerprintResult(error=f"File not found: {file_path}")

    try:
        data = _invoke_fpcalc(file_path)
        return FingerprintResult(
            fingerprint=data.get("fingerprint"),
            duration=data.get("duration"),
        )
    except Exception as exc:
        return FingerprintResult(error=str(exc))


def _invoke_fpcalc(file_path: str) -> dict:
    """Run fpcalc and parse JSON output. Tests mock this function."""
    cmd = ["fpcalc", "-json", file_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"fpcalc failed: {result.stderr}")
    return json.loads(result.stdout)