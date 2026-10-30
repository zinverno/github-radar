"""Bounded libpq I/O: interruption abandons the connection, never proves rollback.

The protocol waiter and connection generator are pinned psycopg 3.3.6 interfaces.
Upgrade them only together with the TLS relay and cancellation regression tests.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Coroutine
from contextvars import ContextVar
from typing import Any, Self, TypeVar

import psycopg
from psycopg import waiting
from psycopg.abc import PQGen, PQGenConn

from github_radar.native import native_report, require_implementation
from github_radar.storage.connection import collector_ca, reject_libpq_environment

R = TypeVar("R")
_connecting: ContextVar[list[PQGenConn[Any]]] = ContextVar("radar_connecting")
logger = logging.getLogger(__name__)


def require_reviewed_driver() -> None:
    """Private cancellation/connection hooks must not drift beneath frozen installs."""
    require_implementation()
    native_report()


class DatabaseIODeadline(psycopg.OperationalError):
    """Local I/O ended without confirmation of the remote transaction outcome."""

    database_outcome_uncertain = True


class DeadlineConnection(psycopg.AsyncConnection[Any]):
    io_seconds: float = 30
    abandoned = False

    @classmethod
    async def connect(cls, conninfo: str = "", **kwargs: Any) -> Self:
        require_reviewed_driver()
        io_seconds = kwargs.pop("io_seconds", 30)
        connect_seconds = kwargs.pop("connect_seconds", 10)
        ca_pem = kwargs.pop("ca_pem", "")
        for limit in (io_seconds, connect_seconds):
            if (
                not isinstance(limit, (int, float))
                or isinstance(limit, bool)
                or not math.isfinite(limit)
                or limit <= 0
            ):
                raise ValueError("Database deadlines must be finite and positive")
        reject_libpq_environment()
        attempts: list[PQGenConn[Any]] = []
        token = _connecting.set(attempts)
        try:
            with collector_ca(kwargs["sslrootcert"], ca_pem) as ca:
                kwargs["sslrootcert"] = ca
                async with asyncio.timeout(connect_seconds):
                    connection = await super().connect(conninfo, **kwargs)
            connection.io_seconds = io_seconds
            return connection
        except (psycopg.Error, OSError, TimeoutError):
            raise psycopg.OperationalError(
                "Database connection failed; details suppressed"
            ) from None
        finally:
            # Retained timeout tracebacks must not keep partial connection sockets alive.
            for attempt in attempts:
                attempt.close()
            _connecting.reset(token)

    @classmethod
    def _connect_gen(cls, conninfo: str = "") -> PQGenConn[Self]:
        attempt = super()._connect_gen(conninfo)
        _connecting.get().append(attempt)
        return attempt

    def abandon(self) -> None:
        self.abandoned = True
        self._closed = True
        self.pgconn.finish()

    async def wait(
        self,
        gen: PQGen[R],
        interval: float = 0.1,
        timeout: float | None = None,
    ) -> R:
        seconds = self.io_seconds if timeout is None else min(self.io_seconds, timeout)
        try:
            async with asyncio.timeout(seconds):
                # Avoid the driver's additional cancel-and-drain waits on interruption.
                return await waiting.wait_async(gen, self.pgconn.socket, interval=interval)
        except TimeoutError:
            self.abandon()
            raise DatabaseIODeadline(
                "Database I/O deadline exceeded; transaction outcome uncertain"
            ) from None
        except asyncio.CancelledError as exc:
            self.abandon()
            setattr(exc, "database_outcome_uncertain", True)
            raise


def outcome_uncertain(exc: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if getattr(current, "database_outcome_uncertain", False) or getattr(
            current, "connection_invalidated", False
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


async def finish_cleanup[T](operation: Coroutine[Any, Any, T]) -> T:
    """Own a shielded cleanup child and consume its failure after caller cancellation.

    Database I/O in the child has its own finite driver deadline. Repeated cancellation
    cannot detach it, reset that deadline or turn a failed commit into a rollback claim.
    """
    task = asyncio.create_task(operation)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    try:
        result = task.result()
    except BaseException:
        if cancelled:
            logger.error("Database cleanup failed; outcome uncertain; details suppressed")
        raise
    if cancelled:
        raise asyncio.CancelledError
    return result
