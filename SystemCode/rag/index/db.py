"""Postgres connection helpers + migrate."""

from __future__ import annotations

import hashlib

import psycopg
import psycopg.conninfo
from pgvector.psycopg import register_vector

from . import config

SCHEMA_VERSION = 1


def connect(dsn: str | None = None, timeout: int = 5, register: bool = True) -> psycopg.Connection:
    conn = psycopg.connect(dsn or config.get_dsn(), connect_timeout=timeout)
    if register:
        try:
            register_vector(conn)
        except psycopg.ProgrammingError:
            # `vector` type does not exist yet (before the first migrate)
            conn.rollback()
    return conn


def describe_dsn(dsn: str | None = None) -> str:
    """host:port/dbname only; never credentials."""
    try:
        info = psycopg.conninfo.conninfo_to_dict(dsn or config.get_dsn())
        return (
            f"{info.get('host', 'localhost')}:{info.get('port', '5432')}/{info.get('dbname', '?')}"
        )
    except Exception:
        return "<unparseable DSN>"


def db_reachable(dsn: str | None = None) -> bool:
    try:
        with psycopg.connect(dsn or config.get_dsn(), connect_timeout=2):
            return True
    except Exception:
        return False


def migrate(dsn: str | None = None) -> str:
    """Apply schema.sql (baseline, version 1, idempotent), then any migrations/NNN_*.sql not yet
    recorded.

    Returns a one-line status. Everything runs in one transaction under an advisory lock.
    """
    sql = config.SCHEMA_SQL.read_text(encoding="utf-8")
    digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    notes = []
    with connect(dsn, register=False) as conn:
        # serialise concurrent migrators
        conn.execute("SELECT pg_advisory_xact_lock(727001)")
        conn.execute(sql)
        row = conn.execute(
            "SELECT sha256 FROM schema_migrations WHERE version = %s", (SCHEMA_VERSION,)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO schema_migrations (version, sha256) VALUES (%s, %s)",
                (SCHEMA_VERSION, digest),
            )
            notes.append(f"applied schema v{SCHEMA_VERSION} ({digest[:12]})")
        elif row[0] != digest:
            conn.execute(
                "UPDATE schema_migrations SET sha256 = %s, applied_at = now() WHERE version = %s",
                (digest, SCHEMA_VERSION),
            )
            notes.append(
                f"re-applied schema v{SCHEMA_VERSION} (sql changed {row[0][:12]} -> {digest[:12]})"
            )
        done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        for path in sorted(config.MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")):
            version = int(path.name[:3])
            if version in done:
                continue
            text = path.read_text(encoding="utf-8")
            conn.execute(text)
            conn.execute(
                "INSERT INTO schema_migrations (version, sha256) VALUES (%s, %s)",
                (version, hashlib.sha256(text.encode("utf-8")).hexdigest()),
            )
            notes.append(f"applied migration {path.name}")
    return (
        "; ".join(notes) or f"schema already current (v{SCHEMA_VERSION}+{len(done) - 1} migrations)"
    )
