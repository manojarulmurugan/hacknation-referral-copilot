"""Thin, swappable shortlist persistence for the Referral Copilot app."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol


ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class ShortlistItem:
    user_name: str
    facility_id: str
    capability_id: str
    query_context: dict
    verdict: str
    distance_km: float
    saved_at: str


class ShortlistRepository(Protocol):
    backend_name: str

    def save(
        self,
        user_name: str,
        facility_id: str,
        capability_id: str,
        query_context: dict,
        verdict: str,
        distance_km: float,
    ) -> bool:
        """Persist an item; return False when the exact item already exists."""

    def list_for_user(self, user_name: str) -> list[ShortlistItem]:
        """Return one user's saved items, newest first."""


def normalize_user_name(user_name: str) -> str:
    name = " ".join((user_name or "").split())
    if not name:
        raise ValueError("Enter your name before saving.")
    return name[:120]


def _serialized_context(query_context: dict) -> str:
    return json.dumps(query_context or {}, sort_keys=True, separators=(",", ":"))


def _item_from_row(row) -> ShortlistItem:
    return ShortlistItem(
        user_name=row[0],
        facility_id=row[1],
        capability_id=row[2],
        query_context=json.loads(row[3]),
        verdict=row[4],
        distance_km=float(row[5]),
        saved_at=row[6],
    )


class SQLiteShortlistRepository:
    backend_name = "SQLite"

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._connection: sqlite3.Connection | None = None
        if self.path == ":memory:":
            self._connection = sqlite3.connect(":memory:")
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        if self._connection is not None:
            return self._connection
        return sqlite3.connect(self.path)

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS shortlist_item (
                    user_name TEXT NOT NULL,
                    facility_id TEXT NOT NULL,
                    capability_id TEXT NOT NULL,
                    query_context TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    distance_km REAL NOT NULL,
                    saved_at TEXT NOT NULL,
                    UNIQUE (user_name, facility_id, capability_id, query_context)
                )
                """
            )
            connection.commit()
        finally:
            if self._connection is None:
                connection.close()

    def save(
        self,
        user_name: str,
        facility_id: str,
        capability_id: str,
        query_context: dict,
        verdict: str,
        distance_km: float,
    ) -> bool:
        connection = self._connect()
        try:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO shortlist_item (
                    user_name, facility_id, capability_id, query_context,
                    verdict, distance_km, saved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalize_user_name(user_name),
                    str(facility_id),
                    str(capability_id),
                    _serialized_context(query_context),
                    str(verdict),
                    float(distance_km),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            connection.commit()
            return cursor.rowcount == 1
        finally:
            if self._connection is None:
                connection.close()

    def list_for_user(self, user_name: str) -> list[ShortlistItem]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT user_name, facility_id, capability_id, query_context,
                       verdict, distance_km, saved_at
                FROM shortlist_item
                WHERE lower(user_name) = lower(?)
                ORDER BY saved_at DESC
                """,
                (normalize_user_name(user_name),),
            ).fetchall()
            return [_item_from_row(row) for row in rows]
        finally:
            if self._connection is None:
                connection.close()


class LakebaseShortlistRepository:
    """Lakebase/Postgres implementation using the App service principal."""

    backend_name = "Lakebase"
    schema_name = "referral_copilot"

    def __init__(self) -> None:
        required = ["ENDPOINT_NAME", "PGHOST", "PGDATABASE", "PGUSER"]
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise RuntimeError(f"Missing Lakebase configuration: {', '.join(missing)}")
        self._initialize()

    def _connect(self):
        import psycopg
        from databricks.sdk import WorkspaceClient

        workspace = WorkspaceClient()
        credential = workspace.postgres.generate_database_credential(
            endpoint=os.environ["ENDPOINT_NAME"]
        )
        return psycopg.connect(
            host=os.environ["PGHOST"],
            dbname=os.environ["PGDATABASE"],
            user=os.environ["PGUSER"],
            password=credential.token,
            port=int(os.environ.get("PGPORT", "5432")),
            sslmode=os.environ.get("PGSSLMODE", "require"),
        )

    def _initialize(self) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"CREATE SCHEMA IF NOT EXISTS {self.schema_name}"
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self.schema_name}.shortlist_item (
                        user_name TEXT NOT NULL,
                        facility_id TEXT NOT NULL,
                        capability_id TEXT NOT NULL,
                        query_context TEXT NOT NULL,
                        verdict TEXT NOT NULL,
                        distance_km DOUBLE PRECISION NOT NULL,
                        saved_at TEXT NOT NULL,
                        UNIQUE (user_name, facility_id, capability_id, query_context)
                    )
                    """
                )

    def save(
        self,
        user_name: str,
        facility_id: str,
        capability_id: str,
        query_context: dict,
        verdict: str,
        distance_km: float,
    ) -> bool:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {self.schema_name}.shortlist_item (
                        user_name, facility_id, capability_id, query_context,
                        verdict, distance_km, saved_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_name, facility_id, capability_id, query_context)
                    DO NOTHING
                    """,
                    (
                        normalize_user_name(user_name),
                        str(facility_id),
                        str(capability_id),
                        _serialized_context(query_context),
                        str(verdict),
                        float(distance_km),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                return cursor.rowcount == 1

    def list_for_user(self, user_name: str) -> list[ShortlistItem]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT user_name, facility_id, capability_id, query_context,
                           verdict, distance_km, saved_at
                    FROM {self.schema_name}.shortlist_item
                    WHERE lower(user_name) = lower(%s)
                    ORDER BY saved_at DESC
                    """,
                    (normalize_user_name(user_name),),
                )
                return [_item_from_row(row) for row in cursor.fetchall()]


def load_shortlist_repository() -> ShortlistRepository:
    backend = os.environ.get("REFERRAL_PERSISTENCE", "").strip().lower()
    if backend == "lakebase" or (not backend and os.environ.get("ENDPOINT_NAME")):
        return LakebaseShortlistRepository()
    path = os.environ.get(
        "REFERRAL_SHORTLIST_DB",
        str(ROOT / "data" / "referral_shortlist.sqlite"),
    )
    return SQLiteShortlistRepository(path)
