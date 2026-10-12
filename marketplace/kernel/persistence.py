"""Database-neutral persistence.

Implements Database Neutrality: the domain has no dependency on a specific
database product. The default adapter is stdlib SQLite, chosen because it adds
no vendor dependency to the platform. Adapters for other engines implement the
same ``UnitOfWork``/``Repository`` contracts.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Optional

from .errors import ConcurrencyConflictError, NotFoundError


class InMemoryStore:
    """Reference store used by the domain repositories and by tests.

    It implements optimistic locking and transactional semantics so that the
    domain logic is identical whether it runs against memory or SQL.
    """

    def __init__(self) -> None:
        self._tables: dict[str, dict[str, dict]] = {}
        self._lock = threading.RLock()

    def table(self, name: str) -> dict[str, dict]:
        return self._tables.setdefault(name, {})

    def put(self, table: str, key: str, value: dict, *, expected_version: Optional[int]) -> int:
        with self._lock:
            row = self.table(table).get(key)
            if row is not None:
                current_version = row.get("_version", 0)
                if expected_version is None or current_version != expected_version:
                    raise ConcurrencyConflictError(
                        f"Optimistic lock failed for {table}/{key}: "
                        f"expected version {expected_version}, found {current_version}"
                    )
                new_version = current_version + 1
            else:
                if expected_version is not None:
                    raise ConcurrencyConflictError(
                        f"Optimistic lock failed for {table}/{key}: row does not exist"
                    )
                new_version = 0
            stored = dict(value, _version=new_version)
            self.table(table)[key] = stored
            return new_version

    def get(self, table: str, key: str) -> dict:
        row = self.table(table).get(key)
        if row is None:
            raise NotFoundError(f"{table}/{key} not found")
        return dict(row)

    def find(self, table: str, predicate) -> list[dict]:
        return [dict(v) for v in self.table(table).values() if predicate(v)]

    def all(self, table: str) -> list[dict]:
        return [dict(v) for v in self.table(table).values()]

    def delete(self, table: str, key: str) -> None:
        self.table(table).pop(key, None)

    def scan(self, table: str, index: str, value: Any) -> list[dict]:
        return [dict(v) for v in self.table(table).values() if v.get(index) == value]


@dataclass
class SchemaMigration:
    version: int
    description: str
    forward: str
    rollback: str


MIGRATIONS: list[SchemaMigration] = [
    SchemaMigration(
        version=1,
        description="core canonical tables",
        forward="""
            CREATE TABLE IF NOT EXISTS projections (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL,
                version INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );
        """,
        rollback="DROP TABLE IF EXISTS projections;",
    ),
]


class SqlProjectionStore:
    """A generated projection store backed by SQLite.

    Projections are derived state (LAW-ARCH-06): they carry the source id and
    version and can always be rebuilt from the event log.
    """

    def __init__(self, path: str = ":memory:") -> None:
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.RLock()
        for migration in MIGRATIONS:
            with self._lock:
                self._connection.execute(migration.forward)
        self._connection.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._connection
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

    def save(self, kind: str, projection_id: str, payload: dict, version: int, updated_at: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO projections (id, kind, payload, version, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, "
                "version=excluded.version, updated_at=excluded.updated_at",
                (projection_id, kind, json.dumps(payload, default=str), version, updated_at),
            )

    def load(self, kind: str, projection_id: str) -> Optional[dict]:
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT payload, version FROM projections WHERE id = ? AND kind = ?",
                (projection_id, kind),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row[0])
        return dict(payload, _source_version=row[1])

    def scan(self, kind: str) -> list[dict]:
        with self.transaction() as conn:
            rows = conn.execute(
                "SELECT payload FROM projections WHERE kind = ?", (kind,)
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def close(self) -> None:
        self._connection.close()
