"""
LCM Connection Pool — Serialized writes + concurrent reads for SQLite.

Solves the "database is locked" problem when CompactionEngine runs
background compaction while the Agent ingests new messages.

Design:
  - Single writer connection protected by threading.Lock
  - Pool of read-only connections for concurrent reads
  - WAL mode + PRAGMA synchronous = NORMAL for write throughput
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Callable, TypeVar

T = TypeVar("T")
logger = logging.getLogger("lcm.db")


class ConnectionPool:
    """
    SQLite connection pool with serialized write access.

    Usage::

        pool = ConnectionPool("/path/to/lcm.db")

        # Write (serialized — only one thread at a time)
        pool.execute_write(lambda conn: conn.execute("INSERT ..."))

        # Read (concurrent — multiple readers)
        result = pool.execute_read(
            lambda conn: conn.execute("SELECT ...").fetchall()
        )

        # Cleanup
        pool.close()
    """

    def __init__(
        self,
        db_path: str,
        max_readers: int = 4,
        busy_timeout_ms: int = 15000,
    ) -> None:
        self._db_path = db_path
        self._max_readers = max_readers
        self._busy_timeout_ms = busy_timeout_ms
        self._write_lock = threading.RLock()
        self._writer: sqlite3.Connection | None = None
        self._readers: Queue[sqlite3.Connection] = Queue(maxsize=max_readers)
        self._closed = False

        # Ensure parent directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    def _create_connection(self, readonly: bool = False) -> sqlite3.Connection:
        """Create a new SQLite connection with optimal pragmas."""
        if readonly:
            uri = f"file:{self._db_path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, check_same_thread=False, isolation_level=None)
        else:
            conn = sqlite3.connect(self._db_path, check_same_thread=False, isolation_level=None)

        # Optimal pragmas for LCM workload
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA cache_size = -8000")  # 8MB cache
        conn.row_factory = sqlite3.Row
        return conn

    def _get_writer(self) -> sqlite3.Connection:
        """Lazily create and return the writer connection."""
        if self._writer is None:
            self._writer = self._create_connection(readonly=False)
            logger.debug("Created writer connection to %s", self._db_path)
        return self._writer

    def _acquire_reader(self) -> sqlite3.Connection:
        """Get a reader connection from the pool or create a new one."""
        try:
            return self._readers.get_nowait()
        except Empty:
            conn = self._create_connection(readonly=True)
            logger.debug("Created new reader connection to %s", self._db_path)
            return conn

    def _release_reader(self, conn: sqlite3.Connection) -> None:
        """Return a reader connection to the pool."""
        try:
            self._readers.put_nowait(conn)
        except Full:
            conn.close()

    def execute_write(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """
        Execute a write operation with serialized access.

        Only one thread can hold the write lock at a time.
        This prevents "database is locked" errors when compaction
        runs concurrently with message ingestion.
        """
        if self._closed:
            raise RuntimeError("ConnectionPool is closed")

        with self._write_lock:
            conn = self._get_writer()
            return fn(conn)

    def execute_read(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """
        Execute a read operation using the connection pool.

        Multiple threads can read concurrently in WAL mode.
        """
        if self._closed:
            raise RuntimeError("ConnectionPool is closed")

        conn = self._acquire_reader()
        try:
            return fn(conn)
        finally:
            self._release_reader(conn)

    def execute_in_transaction(
        self, fn: Callable[[sqlite3.Connection], T]
    ) -> T:
        """
        Execute a write operation inside a BEGIN IMMEDIATE transaction.

        Automatically commits on success, rolls back on exception.
        """
        if self._closed:
            raise RuntimeError("ConnectionPool is closed")

        with self._write_lock:
            conn = self._get_writer()
            conn.execute("BEGIN IMMEDIATE")
            try:
                result = fn(conn)
                conn.execute("COMMIT")
                logger.debug("[lcm.db] transaction COMMITTED")
                return result
            except Exception as e:
                conn.execute("ROLLBACK")
                logger.error("[lcm.db] transaction ROLLED BACK due to: %s", e)
                raise

    @property
    def writer(self) -> sqlite3.Connection:
        """
        Direct access to the writer connection.

        Use with caution — prefer execute_write() for thread safety.
        Only for migration and initialization where we know we're
        single-threaded.
        """
        return self._get_writer()

    def close(self) -> None:
        """Close all connections and mark the pool as closed."""
        self._closed = True

        with self._write_lock:
            if self._writer is not None:
                try:
                    self._writer.close()
                except Exception:
                    pass
                self._writer = None

        while True:
            try:
                conn = self._readers.get_nowait()
                try:
                    conn.close()
                except Exception:
                    pass
            except Empty:
                break

        logger.debug("ConnectionPool closed for %s", self._db_path)

    def __del__(self) -> None:
        if not self._closed:
            self.close()
