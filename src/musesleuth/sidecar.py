"""Per-file sidecar identity records for MuseSleuth track linking."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


SIDECAR_EXT = ".dlpmeta"
SIDECAR_BAK_EXT = ".dlpmeta.bak"
SIDECAR_VERSION = 2
SIGNATURE_FIELD = "signature"
SIGNATURE_ALGO = "HMAC-SHA256"
_SIGNING_KEY_ENV = "MUSESLEUTH_SIDECAR_KEY"

# Top-level payload fields that must be excluded from canonical signing bytes.
# `signature` is the output slot; `updated_at` / `exported_at` are purely
# informational timestamps that would otherwise make round-trips non-idempotent.
_SIGNING_EXCLUDED_FIELDS = frozenset({SIGNATURE_FIELD, "updated_at", "exported_at"})


def get_signing_key() -> Optional[bytes]:
    """Return the HMAC-SHA256 signing key from the environment, or None."""
    raw = os.environ.get(_SIGNING_KEY_ENV)
    if not raw:
        return None
    return raw.encode("utf-8")


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    """Canonical JSON encoding of *payload* used for signing / verification."""
    stripped = {k: v for k, v in payload.items() if k not in _SIGNING_EXCLUDED_FIELDS}
    return json.dumps(
        stripped,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def sign_payload(payload: dict[str, Any], key: Optional[bytes]) -> dict[str, Any]:
    """Build the signature block for *payload*.

    ``sha256`` is always populated (tamper detection without a secret).
    ``hmac`` is populated only when *key* is provided.
    """
    canonical = _canonical_bytes(payload)
    block: dict[str, Any] = {
        "algo": SIGNATURE_ALGO,
        "sha256": hashlib.sha256(canonical).hexdigest(),
        "hmac": (
            hmac.new(key, canonical, hashlib.sha256).hexdigest() if key else None
        ),
    }
    return block


def verify_signature(
    payload: dict[str, Any], key: Optional[bytes]
) -> str:
    """Verify the signature block on *payload*.

    Returns one of:
    - ``"valid"``     — HMAC present and matches *key*.
    - ``"unsigned"``  — no signature block, or HMAC absent/mismatched.
    - ``"corrupt"``   — SHA-256 digest does not match the canonical payload.
    """
    sig = payload.get(SIGNATURE_FIELD)
    if not isinstance(sig, dict):
        return "unsigned"
    canonical = _canonical_bytes(payload)
    expected_sha = hashlib.sha256(canonical).hexdigest()
    if sig.get("sha256") != expected_sha:
        return "corrupt"
    stored_hmac = sig.get("hmac")
    if not stored_hmac or not key:
        return "unsigned"
    expected_hmac = hmac.new(key, canonical, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(stored_hmac, expected_hmac):
        return "unsigned"
    return "valid"


def _win_long(p: str) -> str:
    r"""Prefix with ``\\?\`` on Windows when *p* risks exceeding MAX_PATH."""
    prefix = r"\\?\ "[:-1]  # \\?\
    if os.name == "nt" and len(p) >= 260 and not p.startswith(prefix):
        return prefix + os.path.abspath(p)
    return p


@dataclass
class SidecarData:
    """Data stored in a sidecar identity file."""

    metadata_id: str
    path: str
    size: int
    mtime: float
    hash_partial: Optional[str] = None
    hash_full: Optional[str] = None
    fingerprint: Optional[str] = None
    version: int = 1
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        now = _now_iso()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now


def sidecar_path_for(audio_path: Path) -> Path:
    """Derive the sidecar file path for a given audio file."""
    raw = str(audio_path) + SIDECAR_EXT
    return Path(_win_long(raw))


def build_dlpmeta_payload(data: SidecarData) -> dict[str, Any]:
    """Build the raw JSON payload (without signature) for a ``SidecarData``."""
    return {
        "v": data.version,
        "id": data.metadata_id,
        "path": data.path,
        "size": data.size,
        "mtime": data.mtime,
        "hash_partial": data.hash_partial,
        "hash_full": data.hash_full,
        "fp": data.fingerprint,
        "created_at": data.created_at,
        "updated_at": data.updated_at,
    }


def _atomic_write_json(target: Path, payload: dict[str, Any]) -> None:
    """Atomically write *payload* as pretty JSON to *target* via a sibling tempfile."""
    dir_path = target.parent
    fd, tmp_path = tempfile.mkstemp(dir=str(dir_path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, _win_long(str(target)))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def write_sidecar(
    audio_path: Path,
    data: SidecarData,
    *,
    preserve_created: bool = False,
    sign: bool = False,
    key: Optional[bytes] = None,
    use_bak_when_unsigned: bool = False,
) -> Path:
    """Atomically write a sidecar JSON file beside the audio file.

    Uses temp-file + rename for atomicity. If *preserve_created* is True
    and a sidecar already exists, the ``created_at`` timestamp is preserved.

    Signing:
      - If *sign* is True, an HMAC-SHA256 signature block is embedded (only
        when *key* is provided; otherwise just the SHA-256 digest).
      - If *sign* is True and *key* is None and *use_bak_when_unsigned* is
        True, the file is written to ``<audio>.dlpmeta.bak`` instead of
        ``<audio>.dlpmeta`` so unsigned exports are never mistaken for
        trusted ones.
    """
    sc_path = sidecar_path_for(audio_path)

    if preserve_created and sc_path.exists():
        existing = read_sidecar(audio_path)
        if existing is not None:
            data.created_at = existing.created_at

    data.updated_at = _now_iso()

    payload = build_dlpmeta_payload(data)

    if sign:
        payload[SIGNATURE_FIELD] = sign_payload(payload, key)
        if key is None and use_bak_when_unsigned:
            sc_path = Path(_win_long(str(audio_path) + SIDECAR_BAK_EXT))

    _atomic_write_json(sc_path, payload)
    return sc_path


def sidecar_from_payload(raw: dict[str, Any]) -> SidecarData:
    """Materialize a ``SidecarData`` instance from a parsed ``.dlpmeta`` payload."""
    return SidecarData(
        metadata_id=raw.get("id", ""),
        path=raw.get("path", ""),
        size=raw.get("size", 0),
        mtime=raw.get("mtime", 0.0),
        hash_partial=raw.get("hash_partial"),
        hash_full=raw.get("hash_full"),
        fingerprint=raw.get("fp"),
        version=raw.get("v", 1),
        created_at=raw.get("created_at", ""),
        updated_at=raw.get("updated_at", ""),
    )


def read_sidecar(audio_path: Path) -> Optional[SidecarData]:
    """Read a ``.dlpmeta`` sidecar for the given audio path. Returns None if missing."""
    sc_path = sidecar_path_for(audio_path)
    if not sc_path.exists():
        return None

    try:
        raw = json.loads(sc_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    return sidecar_from_payload(raw)


def read_sidecar_raw(sidecar_path: Path) -> Optional[dict[str, Any]]:
    """Read and parse a ``.dlpmeta`` (or ``.dlpmeta.bak``) file at *sidecar_path*."""
    try:
        return json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()