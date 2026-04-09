from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from musesleuth.db import create_schema, get_connection


@dataclass(frozen=True)
class SchemaSnapshot:
    tables: dict[str, tuple[str, ...]]
    indexes: tuple[str, ...]


@dataclass(frozen=True)
class SchemaDiff:
    missing_tables: tuple[str, ...]
    missing_indexes: tuple[str, ...]
    missing_columns: dict[str, tuple[str, ...]]
    extra_tables: tuple[str, ...]
    extra_indexes: tuple[str, ...]
    extra_columns: dict[str, tuple[str, ...]]

    def has_missing_elements(self) -> bool:
        return bool(self.missing_tables or self.missing_indexes or self.missing_columns)


@dataclass(frozen=True)
class UpgradeReport:
    database_path: Path
    before: SchemaDiff
    after: SchemaDiff
    backup_path: Path | None
    changed: bool
    dry_run: bool


def _quoted_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def snapshot_schema(conn: sqlite3.Connection) -> SchemaSnapshot:
    table_names = sorted(
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    )
    tables = {
        table_name: tuple(
            row[1]
            for row in conn.execute(f"PRAGMA table_info({_quoted_identifier(table_name)})").fetchall()
        )
        for table_name in table_names
    }
    indexes = tuple(
        sorted(
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        )
    )
    return SchemaSnapshot(tables=tables, indexes=indexes)


def build_reference_snapshot() -> SchemaSnapshot:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    create_schema(conn)
    snapshot = snapshot_schema(conn)
    conn.close()
    return snapshot


def diff_schema(current: SchemaSnapshot, reference: SchemaSnapshot) -> SchemaDiff:
    current_tables = set(current.tables)
    reference_tables = set(reference.tables)
    common_tables = sorted(current_tables & reference_tables)
    missing_columns = {
        table_name: tuple(sorted(set(reference.tables[table_name]) - set(current.tables[table_name])))
        for table_name in common_tables
        if set(reference.tables[table_name]) - set(current.tables[table_name])
    }
    extra_columns = {
        table_name: tuple(sorted(set(current.tables[table_name]) - set(reference.tables[table_name])))
        for table_name in common_tables
        if set(current.tables[table_name]) - set(reference.tables[table_name])
    }
    return SchemaDiff(
        missing_tables=tuple(sorted(reference_tables - current_tables)),
        missing_indexes=tuple(sorted(set(reference.indexes) - set(current.indexes))),
        missing_columns=missing_columns,
        extra_tables=tuple(sorted(current_tables - reference_tables)),
        extra_indexes=tuple(sorted(set(current.indexes) - set(reference.indexes))),
        extra_columns=extra_columns,
    )


def backup_database(db_path: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = db_path.with_name(f"{db_path.stem}-{timestamp}.bak{db_path.suffix}")
    source = sqlite3.connect(str(db_path))
    destination = sqlite3.connect(str(backup_path))
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    return backup_path


def upgrade_database(db_path: Path, *, dry_run: bool = False, make_backup: bool = True) -> UpgradeReport:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    reference = build_reference_snapshot()
    conn = get_connection(db_path)
    before_snapshot = snapshot_schema(conn)
    before_diff = diff_schema(before_snapshot, reference)
    conn.close()
    if dry_run:
        return UpgradeReport(
            database_path=db_path,
            before=before_diff,
            after=before_diff,
            backup_path=None,
            changed=False,
            dry_run=True,
        )
    backup_path = backup_database(db_path) if make_backup else None
    conn = get_connection(db_path)
    create_schema(conn)
    after_snapshot = snapshot_schema(conn)
    after_diff = diff_schema(after_snapshot, reference)
    conn.close()
    return UpgradeReport(
        database_path=db_path,
        before=before_diff,
        after=after_diff,
        backup_path=backup_path,
        changed=before_snapshot != after_snapshot,
        dry_run=False,
    )


def _format_mapping(mapping: dict[str, tuple[str, ...]]) -> list[str]:
    lines: list[str] = []
    for key in sorted(mapping):
        values = ", ".join(mapping[key])
        lines.append(f"  {key}: {values}")
    return lines


def format_report(report: UpgradeReport) -> str:
    lines = [f"Database: {report.database_path}"]
    if report.backup_path is not None:
        lines.append(f"Backup: {report.backup_path}")
    lines.append(f"Dry run: {'yes' if report.dry_run else 'no'}")
    lines.append("")
    lines.append("Before:")
    lines.append(f"  Missing tables: {', '.join(report.before.missing_tables) if report.before.missing_tables else '(none)'}")
    lines.append(f"  Missing indexes: {', '.join(report.before.missing_indexes) if report.before.missing_indexes else '(none)'}")
    if report.before.missing_columns:
        lines.append("  Missing columns:")
        lines.extend(_format_mapping(report.before.missing_columns))
    else:
        lines.append("  Missing columns: (none)")
    if report.before.extra_tables:
        lines.append(f"  Extra tables kept: {', '.join(report.before.extra_tables)}")
    if report.before.extra_columns:
        lines.append("  Extra columns kept:")
        lines.extend(_format_mapping(report.before.extra_columns))
    lines.append("")
    lines.append("After:")
    lines.append(f"  Missing tables: {', '.join(report.after.missing_tables) if report.after.missing_tables else '(none)'}")
    lines.append(f"  Missing indexes: {', '.join(report.after.missing_indexes) if report.after.missing_indexes else '(none)'}")
    if report.after.missing_columns:
        lines.append("  Missing columns:")
        lines.extend(_format_mapping(report.after.missing_columns))
    else:
        lines.append("  Missing columns: (none)")
    lines.append(f"Changed: {'yes' if report.changed else 'no'}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-backup", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = upgrade_database(
        Path(args.db),
        dry_run=bool(args.dry_run),
        make_backup=not bool(args.skip_backup),
    )
    print(format_report(report))
    return 0 if not report.after.has_missing_elements() else 1


if __name__ == "__main__":
    raise SystemExit(main())
