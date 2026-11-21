"""Persistence for rehearsal documents.

Every rehearsal is one JSON document, so every state transition is a single
row update and can never be half-applied.  Two implementations are provided:

* :class:`MemoryStore` -- in-process dictionary, used by the tests.
* :class:`SqliteStore` -- one file, one table, survives a restart.

Writes go through :meth:`Store.replace`, which is compare-and-set on a
monotonically increasing ``version``.  Two concurrent writers therefore cannot
silently clobber each other: the loser gets :class:`StoreConflict` and the
engine retries its (pure, already validated) transition.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from abc import ABC, abstractmethod
from typing import Any


class StoreConflict(Exception):
    """The document changed between read and write."""


class Store(ABC):
    @abstractmethod
    def initialize(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def get(self, rehearsal_id: str) -> tuple[dict[str, Any], int] | None:
        """Return ``(document, version)`` or ``None``."""

    @abstractmethod
    def create(self, document: dict[str, Any]) -> int:
        """Insert a new document; returns version 1."""

    @abstractmethod
    def replace(
        self, rehearsal_id: str, expected_version: int, document: dict[str, Any]
    ) -> int:
        """Atomically replace ``document`` if the stored version matches."""


class MemoryStore(Store):
    def __init__(self) -> None:
        self._docs: dict[str, tuple[dict[str, Any], int]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _copy(document: dict[str, Any]) -> dict[str, Any]:
        return json.loads(json.dumps(document))

    def initialize(self) -> None:  # pragma: no cover - nothing to do
        pass

    def close(self) -> None:  # pragma: no cover - nothing to do
        pass

    def get(self, rehearsal_id: str) -> tuple[dict[str, Any], int] | None:
        with self._lock:
            entry = self._docs.get(rehearsal_id)
            if entry is None:
                return None
            return self._copy(entry[0]), entry[1]

    def create(self, document: dict[str, Any]) -> int:
        with self._lock:
            rehearsal_id = document["rehearsal_id"]
            if rehearsal_id in self._docs:
                raise StoreConflict(rehearsal_id)
            self._docs[rehearsal_id] = (self._copy(document), 1)
            return 1

    def replace(
        self, rehearsal_id: str, expected_version: int, document: dict[str, Any]
    ) -> int:
        with self._lock:
            entry = self._docs.get(rehearsal_id)
            if entry is None or entry[1] != expected_version:
                raise StoreConflict(rehearsal_id)
            version = expected_version + 1
            self._docs[rehearsal_id] = (self._copy(document), version)
            return version


class SqliteStore(Store):
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            parent = os.path.dirname(os.path.abspath(self._path))
            os.makedirs(parent, exist_ok=True)
            self._conn = sqlite3.connect(
                self._path, check_same_thread=False, isolation_level=None
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.execute("PRAGMA busy_timeout=5000")
        return self._conn

    def initialize(self) -> None:
        with self._lock:
            self._connect().execute(
                """
                CREATE TABLE IF NOT EXISTS rehearsals (
                    rehearsal_id TEXT PRIMARY KEY,
                    doc          TEXT    NOT NULL,
                    version      INTEGER NOT NULL
                )
                """
            )

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def get(self, rehearsal_id: str) -> tuple[dict[str, Any], int] | None:
        with self._lock:
            row = self._connect().execute(
                "SELECT doc, version FROM rehearsals WHERE rehearsal_id = ?",
                (rehearsal_id,),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row[0]), row[1]

    def create(self, document: dict[str, Any]) -> int:
        rehearsal_id = document["rehearsal_id"]
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO rehearsals (rehearsal_id, doc, version) VALUES (?, ?, 1)",
                    (rehearsal_id, json.dumps(document)),
                )
            except sqlite3.IntegrityError as exc:
                raise StoreConflict(rehearsal_id) from exc
            return 1

    def replace(
        self, rehearsal_id: str, expected_version: int, document: dict[str, Any]
    ) -> int:
        with self._lock:
            cursor = self._connect().execute(
                "UPDATE rehearsals SET doc = ?, version = version + 1 "
                "WHERE rehearsal_id = ? AND version = ?",
                (json.dumps(document), rehearsal_id, expected_version),
            )
            if cursor.rowcount != 1:
                raise StoreConflict(rehearsal_id)
            return expected_version + 1
