"""Analyze stage runner -- orchestrates BPM/key, fingerprinting, hashing for a single track."""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

from musesleuth.bpm_analyzer import (
    analyze_bpm_aubio,
    analyze_bpm_librosa,
    analyze_energy,
    analyze_key,
    clear_audio_cache,
    cross_check_bpm,
)
from musesleuth.fingerprint import run_fpcalc
from musesleuth.hasher import hash_partial, hash_full
from musesleuth.sidecar import read_sidecar, write_sidecar
from musesleuth.spectral_qc import analyze_spectrogram_quality


@dataclass
class AnalyzeStageResult:
    """Result of the analyze stage for a single track."""

    success: bool
    metadata_id: str = ""
    error: Optional[str] = None


def run_analyze_for_track(
    conn: sqlite3.Connection,
    metadata_id: str,
    file_path: str,
) -> AnalyzeStageResult:

    """Run the full analyze stage for a single track.

    1. Compute partial and full BLAKE3 hashes
    2. Run fpcalc for Chromaprint fingerprint
    3. Run aubio + librosa BPM estimation with cross-check
    4. Run key estimation
    5. Run energy / dynamics analysis
    6. Store results in DB
    7. Update sidecar file
    """
    log.debug("analyze: mid=%s path=%s", metadata_id, file_path)
    path = Path(file_path)

    try:
        if not path.exists():
            log.warning("analyze: file not found mid=%s path=%s", metadata_id, file_path)
            return AnalyzeStageResult(
                success=False,
                metadata_id=metadata_id,
                error=f"File not found: {file_path}",
            )

        # 1. Hashes
        try:
            partial = hash_partial(path)
        except Exception:
            partial = None
        try:
            full = hash_full(path)
        except Exception:
            full = None

        # 2. Fingerprint
        fp_result = run_fpcalc(file_path)

        # 3. BPM cross-check
        bpm_a = analyze_bpm_aubio(file_path)
        bpm_b = analyze_bpm_librosa(file_path)
        bpm_xcheck = cross_check_bpm(bpm_a, bpm_b)

        # 4. Key estimation
        key_result = analyze_key(file_path)

        # 5. Energy / dynamics analysis (reuses cached audio)
        energy_result = analyze_energy(file_path)

        # 6. Spectrogram QC metrics + preview image
        try:
            spec_qc = analyze_spectrogram_quality(
                file_path,
                output_path=str(path) + ".spectrogram.pgm",
            )
        except Exception:
            spec_qc = {
                "lowpass_cutoff_hz": None,
                "silence_ratio": None,
                "clipping_ratio": None,
                "spectrogram_path": None,
            }

        # 7. Store musical features
        conn.execute(
            """
            INSERT OR REPLACE INTO musical_features
                (metadata_id, bpm_aubio, bpm_librosa, bpm_final, bpm_confidence,
                 bpm_disagreement, key_name, key_mode,
                 energy, dynamic_range, onset_density, peak_rms,
                 analyzed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                metadata_id,
                bpm_xcheck.bpm_aubio,
                bpm_xcheck.bpm_librosa,
                bpm_xcheck.bpm_final,
                bpm_xcheck.confidence,
                1 if bpm_xcheck.disagreement else 0,
                key_result.key,
                key_result.mode,
                energy_result.energy,
                energy_result.dynamic_range,
                energy_result.onset_density,
                energy_result.peak_rms,
            ),
        )

        # 8. Store spectrogram-derived quality metrics in technical_features.
        update_cursor = conn.execute(
            """
            UPDATE technical_features
            SET lowpass_cutoff_hz = ?,
                silence_ratio = ?,
                clipping_ratio = ?,
                spectrogram_path = ?,
                spectrogram_generated_at = datetime('now')
            WHERE metadata_id = ?
            """,
            (
                spec_qc.get("lowpass_cutoff_hz"),
                spec_qc.get("silence_ratio"),
                spec_qc.get("clipping_ratio"),
                spec_qc.get("spectrogram_path"),
                metadata_id,
            ),
        )
        if update_cursor.rowcount == 0:
            conn.execute(
                """
                INSERT INTO technical_features
                    (metadata_id, lowpass_cutoff_hz, silence_ratio, clipping_ratio,
                     spectrogram_path, spectrogram_generated_at, analyzed_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (
                    metadata_id,
                    spec_qc.get("lowpass_cutoff_hz"),
                    spec_qc.get("silence_ratio"),
                    spec_qc.get("clipping_ratio"),
                    spec_qc.get("spectrogram_path"),
                ),
            )

        # 9. Store/update sidecar record in DB
        conn.execute(
            """
            INSERT OR REPLACE INTO track_sidecars
                (metadata_id, sidecar_path, hash_partial, hash_full, fingerprint, synced_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                metadata_id,
                str(path) + ".dlpmeta",
                partial,
                full,
                fp_result.fingerprint,
            ),
        )

        conn.commit()

        # 10. Update sidecar file if it exists
        existing_sc = read_sidecar(path)
        if existing_sc is not None:
            existing_sc.hash_partial = partial
            existing_sc.hash_full = full
            existing_sc.fingerprint = fp_result.fingerprint
            stat = path.stat()
            existing_sc.size = stat.st_size
            existing_sc.mtime = stat.st_mtime
            write_sidecar(path, existing_sc, preserve_created=True)

        log.debug(
            "analyze: mid=%s ok bpm=%.1f key=%s-%s energy=%.2f",
            metadata_id,
            bpm_xcheck.bpm_final or 0,
            key_result.key or "?",
            key_result.mode or "?",
            energy_result.energy or 0,
        )
        return AnalyzeStageResult(success=True, metadata_id=metadata_id)
    finally:
        clear_audio_cache()