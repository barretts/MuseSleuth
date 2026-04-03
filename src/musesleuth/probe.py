"""Parse ffprobe JSON output into structured technical metadata."""
from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class IntegrityResult:
    """Result of an audio file integrity check."""

    ok: bool = True
    errors: list[str] = field(default_factory=list)
    was_repaired: bool = False
    repair_attempted: bool = False

    @property
    def error_count(self) -> int:
        return len(self.errors)

    def to_json(self) -> str:
        return json.dumps(self.errors) if self.errors else "[]"


@dataclass
class TechnicalMetadata:
    """Structured technical metadata extracted from ffprobe output."""

    codec: Optional[str] = None
    bitrate: Optional[int] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    duration_ms: Optional[int] = None
    format_tags: Optional[dict[str, str]] = None
    raw_json: Optional[str] = None
    integrity: Optional[IntegrityResult] = None


def parse_ffprobe_output(data: dict) -> TechnicalMetadata:
    """Parse ffprobe JSON dict into TechnicalMetadata.

    Expects the output format from: ffprobe -v quiet -print_format json -show_streams -show_format
    """
    result = TechnicalMetadata()

    if not data:
        return result

    result.raw_json = json.dumps(data, ensure_ascii=False)

    # Extract from first audio stream
    streams = data.get("streams", [])
    audio_stream = None
    for s in streams:
        if s.get("codec_type") == "audio":
            audio_stream = s
            break

    if audio_stream:
        result.codec = audio_stream.get("codec_name")
        result.channels = audio_stream.get("channels")

        sr = audio_stream.get("sample_rate")
        if sr is not None:
            try:
                result.sample_rate = int(sr)
            except (ValueError, TypeError):
                pass

    # Extract from format section
    fmt = data.get("format", {})

    br = fmt.get("bit_rate") or (audio_stream.get("bit_rate") if audio_stream else None)
    if br is not None:
        try:
            result.bitrate = int(br)
        except (ValueError, TypeError):
            pass

    duration_str = fmt.get("duration")
    if duration_str is None and audio_stream:
        duration_str = audio_stream.get("duration")
    if duration_str is not None:
        try:
            result.duration_ms = int(float(duration_str) * 1000)
        except (ValueError, TypeError):
            pass

    # Format-level tags
    tags = fmt.get("tags")
    if tags:
        result.format_tags = {k.lower(): v for k, v in tags.items()}

    return result


# Patterns that indicate real file corruption (not just informational noise)
_CORRUPTION_PATTERNS = [
    (re.compile(r"Header missing", re.IGNORECASE), "missing_header"),
    (re.compile(r"Illegal Audio-MPEG-Header|Invalid MPEG audio header", re.IGNORECASE), "bad_mpeg_header"),
    (re.compile(r"Trying to resync|resync", re.IGNORECASE), "resync_needed"),
    (re.compile(r"overread|overread, currentOffset", re.IGNORECASE), "frame_overread"),
    (re.compile(r"Error while decoding", re.IGNORECASE), "decode_error"),
    (re.compile(r"invalid data found", re.IGNORECASE), "invalid_data"),
    (re.compile(r"could not find codec", re.IGNORECASE), "missing_codec"),
    (re.compile(r"Discarding ID3", re.IGNORECASE), "malformed_id3"),
    (re.compile(r"CRC mismatch|crc error", re.IGNORECASE), "crc_error"),
    (re.compile(r"Giving up resync", re.IGNORECASE), "resync_failed"),
    (re.compile(r"Skipped \d+ bytes", re.IGNORECASE), "skipped_bytes"),
    (re.compile(r"truncat", re.IGNORECASE), "truncated"),
    (re.compile(r"invalid frame size|big_values too big", re.IGNORECASE), "bad_frame"),
    (re.compile(r"mismatching block_size", re.IGNORECASE), "block_size_mismatch"),
]


def check_integrity(file_path: str, timeout: int = 60) -> IntegrityResult:
    """Decode the full file with ffmpeg to detect corruption.

    Runs: ffmpeg -v error -i file -f null -
    This forces a full decode, and any errors/warnings are captured.
    """
    cmd = [
        "ffmpeg",
        "-v", "error",
        "-i", file_path,
        "-f", "null",
        "-",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return IntegrityResult(ok=False, errors=["decode_timeout"])
    except FileNotFoundError:
        logger.warning("ffmpeg not found -- skipping integrity check")
        return IntegrityResult(ok=True, errors=[])

    stderr = proc.stderr.strip()
    if not stderr:
        return IntegrityResult(ok=True, errors=[])

    issues: list[str] = []
    seen: set[str] = set()
    for pattern, label in _CORRUPTION_PATTERNS:
        if pattern.search(stderr) and label not in seen:
            issues.append(label)
            seen.add(label)

    if not issues and stderr:
        issues.append("unknown_error")

    return IntegrityResult(ok=len(issues) == 0, errors=issues)


AUDIO_EXTENSIONS = frozenset({".mp3", ".flac", ".ogg", ".m4a", ".wav", ".aac", ".wma", ".opus"})
_REMUXABLE_EXTENSIONS = AUDIO_EXTENSIONS


def repair_remux(file_path: str, timeout: int = 60) -> bool:
    """Attempt lossless repair via ffmpeg re-mux (-c copy).

    Writes to a temp file in the same directory, then atomically replaces the
    original. Returns True if the re-mux succeeded (ffmpeg exit 0).
    """
    import os
    from pathlib import Path

    path = Path(file_path)
    if path.suffix.lower() not in _REMUXABLE_EXTENSIONS:
        logger.info("Skipping repair for unsupported format: %s", path.suffix)
        return False

    tmp_path = path.with_suffix(f".tmp_repair{path.suffix}")

    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(path),
        "-c", "copy",
        str(tmp_path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        logger.warning("Repair timed out for %s", file_path)
        tmp_path.unlink(missing_ok=True)
        return False
    except FileNotFoundError:
        logger.warning("ffmpeg not found -- cannot repair")
        return False

    if proc.returncode != 0 or not tmp_path.exists():
        logger.warning("Repair failed for %s (exit %d)", file_path, proc.returncode)
        tmp_path.unlink(missing_ok=True)
        return False

    if tmp_path.stat().st_size < 1024:
        logger.warning("Repaired file suspiciously small, keeping original: %s", file_path)
        tmp_path.unlink(missing_ok=True)
        return False

    import stat

    try:
        st = path.stat()
        if not (st.st_mode & stat.S_IWRITE):
            os.chmod(str(path), st.st_mode | stat.S_IWRITE)
        os.replace(str(tmp_path), str(path))
        logger.info("Repaired: %s", file_path)
        return True
    except OSError as e:
        logger.error("Failed to replace original with repaired file: %s", e)
        tmp_path.unlink(missing_ok=True)
        return False