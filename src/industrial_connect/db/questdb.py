from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from threading import RLock
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    import questdb

from .._optional import require
from ..config import EnvConfig
from ..errors import ConnectionError, OperationError, _cleanup
from ..logging import get_logger
from .types import Row

logger = get_logger(__name__)


def _binds(params: Sequence[Any] | None) -> list[Any] | None:
    if params is None:
        return None
    if not isinstance(params, Sequence) or isinstance(params, (str, bytes, bytearray)):
        raise ValueError("QuestDB parameters must be a sequence for positional $1, $2 binds")
    return list(params)


@dataclass(frozen=True, kw_only=True)
class QuestDbConfig(EnvConfig):
    _env_prefix = "QUESTDB_"
    conf: str = field(repr=False)

    def __post_init__(self):
        if not self.conf.startswith(("ws::", "wss::")):
            raise ValueError("Use an explicit QuestDB ws:: or wss:: QWP configuration")


class QuestDbClient:
    """QWP ingestion and SQL over one lazy pool, with serialized caller access."""

    def __init__(self, config: QuestDbConfig) -> None:
        self.config = config
        self._db: questdb.QuestDB | None = None
        self._sender: questdb.PooledSender | None = None
        self._closed = False
        self._lock = RLock()

    def _check_open(self) -> None:
        if self._closed:
            raise ConnectionError("QuestDB client is closed")

    def _handle(self) -> questdb.QuestDB:
        self._check_open()
        if self._db is None:
            driver = require("questdb", "questdb")
            try:
                self._db = driver.connect(self.config.conf)
                logger.info("QuestDB QWP pool initialized")
            except Exception as exc:
                raise ConnectionError("Could not initialize QuestDB QWP pool") from exc
        assert self._db is not None
        return self._db

    def row(
        self,
        table: str,
        *,
        symbols: Mapping[str, str],
        columns: Mapping[str, Any],
        timestamp_ns: int,
    ) -> None:
        if not table or isinstance(timestamp_ns, bool) or not isinstance(timestamp_ns, int):
            raise ValueError("table and integer timestamp_ns are required")
        with self._lock:
            db = self._handle()
            driver = require("questdb", "questdb")
            try:
                if self._sender is None:
                    self._sender = db.sender()
                self._sender.row(
                    table,
                    symbols=dict(symbols),
                    columns=dict(columns),
                    at=driver.TimestampNanos(timestamp_ns),
                )
            except Exception as exc:
                logger.warning("QuestDB QWP ingestion failed; delivery may be uncertain")
                raise OperationError("QuestDB ingestion failed; delivery may be uncertain") from exc

    def flush(self) -> None:
        """Wait for delivery and surface server rejections, including dropped diagnostics."""
        with self._lock:
            self._check_open()
            if self._sender is not None:
                try:
                    self._sender.flush(wait=True)
                    rejected = False
                    while self._sender.poll_error() is not None:
                        rejected = True
                    if rejected or self._sender.error_events_dropped():
                        logger.warning("QuestDB server reported ingestion errors")
                        raise OperationError("QuestDB server reported ingestion errors")
                except OperationError:
                    raise
                except Exception as exc:
                    logger.warning("QuestDB flush failed; delivery may be uncertain")
                    raise OperationError("QuestDB flush failed; delivery may be uncertain") from exc

    def query(self, sql: str, params: Sequence[Any] | None = None) -> list[Row]:
        """Fetch dictionary rows using positional $1, $2 binds; no DataFrame dependency."""
        binds = _binds(params)
        with self._lock:
            db = self._handle()
            try:
                with db.query(sql, binds) as result:
                    names = [name for name, _ in result.columns()]
                    return [dict(zip(names, row, strict=True)) for row in result.iter_rows()]
            except Exception as exc:
                logger.warning("QuestDB query failed")
                raise OperationError("QuestDB query failed") from exc

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> int | None:
        """Execute SQL and return the native row count when provided, without retrying."""
        binds = _binds(params)
        with self._lock:
            db = self._handle()
            try:
                report = db.execute(sql, binds)
                return None if report is None else report.rows_affected
            except Exception as exc:
                logger.warning("QuestDB SQL execution failed; outcome may be uncertain")
                raise OperationError(
                    "QuestDB SQL execution failed; outcome may be uncertain"
                ) from exc

    def close(self, *, flush: bool = True) -> None:
        with self._lock:
            if self._closed and self._sender is None and self._db is None:
                return
            try:
                if flush and not self._closed:
                    self.flush()
            finally:
                self._closed = True
                try:
                    sender = self._sender
                    if sender is not None:
                        if _cleanup(
                            lambda: sender.close(flush=False),
                            "Could not close QuestDB sender resources",
                        ):
                            self._sender = None
                finally:
                    db = self._db
                    if db is not None and _cleanup(
                        lambda: db.close(timeout=0), "Could not close QuestDB pool resources"
                    ):
                        self._db = None
                        logger.info("QuestDB QWP pool closed")

    def __enter__(self) -> Self:
        self._check_open()
        return self

    def __exit__(self, exc_type: type[BaseException] | None, *_: Any) -> None:
        _cleanup(lambda: self.close(flush=exc_type is None), "QuestDB cleanup also failed")
