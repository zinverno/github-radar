"""Real production driver under TLS-transparent, deterministic loopback faults."""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

import psycopg
import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import URL as SQLAlchemyURL
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection

from github_radar.storage.collection import CollectionLockError, locked_collection_session
from github_radar.storage.connection import connection_options
from github_radar.storage.db import ManagedSession, make_engine
from github_radar.storage.deadlines import DeadlineConnection, outcome_uncertain
from tests.integration.safety import validate_test_database_url
from tests.integration.test_bounded_collection import database as database
from tests.integration.tls_relay import Relay

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="test URL unset"),
]
DETAIL: dict[str, Any] = {}
CONFIG: dict[str, Any] = {}
URL: SQLAlchemyURL


@pytest.fixture(autouse=True)
async def fault_fixture(database):
    global URL
    value = os.environ["TEST_DATABASE_URL"]
    validate_test_database_url(test_url=value, app_url=os.environ.get("DATABASE_URL"))
    URL = make_url(value)
    assert URL.host == "localhost" and URL.database == "github_radar_test"
    CONFIG.update(port=URL.port, ca=URL.query["sslrootcert"])
    DETAIL.clear()
    async with database.begin() as c:
        await c.execute(
            text("CREATE TABLE gate_ledger (id text PRIMARY KEY, value integer NOT NULL)")
        )
    try:
        yield
        print(json.dumps(DETAIL))
    finally:
        async with database.begin() as c:
            await c.execute(text("DROP TABLE gate_ledger"))


def kwargs(port=None, statement_ms=30000):
    url, args = connection_options(URL.render_as_string(hide_password=False))
    args.update(url.translate_connect_args(username="user", database="dbname"))
    args.update(
        hostaddr="127.0.0.1",
        port=port or CONFIG["port"],
        options=f"-c timezone=UTC -c statement_timeout={statement_ms} -c lock_timeout=1000",
    )
    return args


def engine(port=None, *, seconds=0.3, public=False, statement_ms=30000):
    value = URL.set(port=port or CONFIG["port"]).render_as_string(hide_password=False)
    e = make_engine(value, public=public, null_pool=not public)

    @event.listens_for(e.sync_engine, "do_connect")
    def test_budgets(dialect, record, args, params):
        params["io_seconds"] = seconds
        params["options"] += f" -c statement_timeout={statement_ms}"

    return e


async def observer(sql, params=()):
    async with asyncio.timeout(3):
        async with await DeadlineConnection.connect(**kwargs(), autocommit=True) as c:
            cursor = await c.execute(sql, params)
            return await cursor.fetchall() if cursor.description else []


async def until(check, seconds=2):
    async with asyncio.timeout(seconds):
        while not await check():
            await asyncio.sleep(0.01)


async def no_lock():
    return not (
        await observer("SELECT 1 FROM pg_locks WHERE locktype='advisory' AND objid=6742565")
    )


async def barrier(event_):
    await asyncio.wait_for(event_.wait(), 3)


async def failure(awaitable, *, maximum=1.3, minimum=0):
    start = time.monotonic()
    try:
        await awaitable
    except BaseException as exc:
        elapsed = time.monotonic() - start
        assert not isinstance(exc, AssertionError)
        assert minimum <= elapsed < maximum, (elapsed, minimum, maximum)
        DETAIL.setdefault("failures", []).append(
            {
                "seconds": round(elapsed, 4),
                "type": type(exc).__name__,
                "uncertain": outcome_uncertain(exc),
            }
        )
        return exc
    raise AssertionError("Operation unexpectedly succeeded")


async def security_utc():
    e = engine()
    attempts = []
    event.listen(e.sync_engine, "do_connect", lambda *args: attempts.append(True))
    try:
        async with e.connect() as c:
            raw = (await c.get_raw_connection()).driver_connection
            assert raw.pgconn.ssl_in_use
            params = raw.info.get_parameters()
            assert params["sslmode"] == "verify-full" and params["channel_binding"] == "require"
            pids, stamps = [], []
            for _ in range(3):
                row = (
                    await c.execute(
                        text("SELECT pg_backend_pid(), current_setting('TimeZone'), now()")
                    )
                ).one()
                pids.append(row[0])
                stamps.append(row[2])
                assert row[1] == "UTC" and row[2].utcoffset().total_seconds() == 0
                await c.commit()
            assert len(set(pids)) == 1 and len(attempts) == 1
            tls = await c.scalar(
                text("SELECT version FROM pg_stat_ssl WHERE pid=pg_backend_pid() AND ssl")
            )
            assert tls == "TLSv1.3"
            DETAIL.update(
                tls=tls,
                channel_binding_required=True,
                stable_backend=True,
                utc_transactions=3,
                do_connect_event_observed=True,
            )
    finally:
        await e.dispose()


async def negative_security():
    for change in (
        {"host": "wrong.invalid"},
        {"sslrootcert": "/etc/ssl/certs/ca-certificates.crt"},
        {"sslmode": "disable"},
        {"channel_binding": "invalid"},
    ):
        args = kwargs()
        args.update(change)
        exc = await failure(DeadlineConnection.connect(**args, connect_seconds=0.5))
        assert isinstance(exc, psycopg.Error)
    DETAIL["rejected"] = [
        "hostname_mismatch",
        "untrusted_CA",
        "no_TLS_with_required_binding",
        "invalid_binding",
    ]


async def connect_stall(seconds=0.25):
    accepted, eof = asyncio.Event(), asyncio.Event()
    writers = []

    async def accept(reader, writer):
        writers.append(writer)
        await reader.readexactly(8)  # PostgreSQL SSLRequest; no SSL response is sent.
        accepted.set()
        assert await reader.read() == b""
        eof.set()

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    try:
        args = kwargs(server.sockets[0].getsockname()[1])
        args["connect_timeout"] = max(2, int(seconds))
        task = asyncio.create_task(DeadlineConnection.connect(**args, connect_seconds=seconds))
        await barrier(accepted)
        exc = await failure(task, minimum=seconds - 0.1, maximum=seconds + 1)
        assert isinstance(exc, psycopg.OperationalError)
        await barrier(eof)
        DETAIL["handshake_socket_closed"] = True
        DETAIL["connect_budget_seconds"] = seconds
        DETAIL["transaction_started"] = False
    finally:
        for writer in writers:
            writer.close()
        server.close()
        await server.wait_closed()


async def dns_stall():
    loop = asyncio.get_running_loop()
    original = loop.getaddrinfo
    started = asyncio.Event()

    async def hang(*args, **kw):
        started.set()
        await asyncio.Event().wait()

    setattr(loop, "getaddrinfo", hang)
    try:
        args = kwargs()
        args.pop("hostaddr")
        task = asyncio.create_task(DeadlineConnection.connect(**args, connect_seconds=0.25))
        await barrier(started)
        assert isinstance(await failure(task, minimum=0.15), psycopg.OperationalError)
        DETAIL["transaction_started"] = False
    finally:
        setattr(loop, "getaddrinfo", original)


async def long_query():
    e = engine()
    try:
        async with e.connect() as c:
            pid = await c.scalar(text("SELECT pg_backend_pid()"))
            raw = (await c.get_raw_connection()).driver_connection
            await c.commit()
            exc = await failure(c.execute(text("SELECT pg_sleep(3)")), minimum=0.25)
            assert outcome_uncertain(exc) and c.invalidated and raw.closed
            DETAIL["server_still_executing_after_client_failure"] = bool(
                await observer(
                    "SELECT 1 FROM pg_stat_activity WHERE pid=%s AND wait_event='PgSleep'", (pid,)
                )
            )
            assert DETAIL["server_still_executing_after_client_failure"]
            # Only the owned synthetic query is terminated for fixture cleanup.
            await observer("SELECT pg_terminate_backend(%s)", (pid,))
    finally:
        await e.dispose()


async def statement_timeout():
    e = engine(seconds=0.5, statement_ms=70)
    try:
        async with e.connect() as c:
            exc = await failure(c.execute(text("SELECT pg_sleep(3)")))
            assert exc.orig.sqlstate == "57014" and not c.invalidated
            await c.rollback()
            assert await c.scalar(text("SELECT 1")) == 1
            DETAIL["sqlstate"] = "57014"
            DETAIL["normal_server_cancellation_reusable_after_rollback"] = True
    finally:
        await e.dispose()


async def blackhole(seconds=0.3):
    async with Relay(CONFIG["port"]) as relay:
        e = engine(relay.port, seconds=seconds, statement_ms=50)
        try:
            async with e.connect() as c:
                await c.execute(text("SELECT 1"))
                await c.commit()
                pid = await c.scalar(text("SELECT pg_backend_pid()"))
                raw = (await c.get_raw_connection()).driver_connection
                link = relay.links[-1]
                link.arm()
                started = time.monotonic()
                task = asyncio.create_task(c.execute(text("SELECT pg_sleep(30)")))
                await barrier(link.held)  # Server's 50ms cancellation cannot reach client.
                state = (await observer("SELECT state FROM pg_stat_activity WHERE pid=%s", (pid,)))[
                    0
                ][0]
                assert state == "idle in transaction (aborted)"
                exc = await failure(task, maximum=seconds + 1, minimum=max(0, seconds - 0.2))
                assert outcome_uncertain(exc) and c.invalidated and raw.closed
                await barrier(link.client_eof)
                DETAIL.update(
                    client_budget_seconds=seconds,
                    server_statement_timeout_ms=50,
                    connection_invalidated=True,
                    reply_received_by_relay=True,
                    backend_state=state,
                    operation_seconds=round(time.monotonic() - started, 4),
                )
        finally:
            await e.dispose()


async def commit_fault(direction):
    await until(no_lock)
    async with Relay(CONFIG["port"]) as relay:
        e = engine(relay.port)
        connects = []
        event.listen(e.sync_engine, "connect", lambda *args: connects.append(True))
        try:
            async with locked_collection_session(e) as session:
                await session.execute(
                    text("INSERT INTO gate_ledger VALUES (:id, 1)"), {"id": f"commit-{direction}"}
                )
                link = relay.links[-1]

                def before_commit(c):
                    link.arm(direction)

                event.listen(e.sync_engine, "commit", before_commit, once=True)
                task = asyncio.create_task(session.commit())
                await barrier(link.held)
                rows = await observer(
                    "SELECT id FROM gate_ledger WHERE id=%s", (f"commit-{direction}",)
                )
                assert bool(rows) == (direction == "down")
                exc = await failure(task)
                assert isinstance(exc, CollectionLockError) and outcome_uncertain(exc)
                assert isinstance(session.bind, AsyncConnection) and session.bind.invalidated
                await session.rollback()
                exc2 = await failure(
                    session.execute(text("INSERT INTO gate_ledger VALUES ('replay', 1)"))
                )
                assert isinstance(exc2, CollectionLockError) and len(connects) == 1
                DETAIL.update(
                    server_committed=bool(rows),
                    outcome="uncertain",
                    new_connections_after_loss=0,
                    replay_rows=len(await observer("SELECT id FROM gate_ledger WHERE id='replay'")),
                )
                assert DETAIL["replay_rows"] == 0
        finally:
            await e.dispose()
    await until(no_lock)


async def cancelled_ownership():
    await until(no_lock)
    async with Relay(CONFIG["port"]) as relay:
        e = engine(relay.port, seconds=15)
        try:
            async with locked_collection_session(e) as session:
                await session.execute(
                    text("INSERT INTO gate_ledger VALUES ('ownership-earlier', 1)")
                )
                await session.commit()
                await session.execute(
                    text("INSERT INTO gate_ledger VALUES ('ownership-cancelled', 1)")
                )
                link = relay.links[-1]

                def arm(c, cursor, statement, params, ctx, many):
                    if "FROM pg_locks" in statement:
                        link.arm()

                event.listen(e.sync_engine, "before_cursor_execute", arm)
                task = asyncio.create_task(session.commit())
                await barrier(link.held)
                start = time.monotonic()
                task.cancel()
                exc = await failure(task)
                DETAIL["cancellation_to_failure_seconds"] = round(time.monotonic() - start, 4)
                assert isinstance(exc, CollectionLockError) and outcome_uncertain(exc)
                assert isinstance(session.bind, AsyncConnection) and session.bind.invalidated
                assert (
                    len(await observer("SELECT id FROM gate_ledger WHERE id='ownership-earlier'"))
                    == 1
                )
                assert not await observer(
                    "SELECT id FROM gate_ledger WHERE id='ownership-cancelled'"
                )
                await session.rollback()
                assert isinstance(await failure(session.commit()), CollectionLockError)
                DETAIL["prior_commit_preserved"] = True
        finally:
            await e.dispose()
    await until(no_lock)


async def contender_stall():
    await until(no_lock)
    async with Relay(CONFIG["port"]) as relay:
        a, b = engine(relay.port), engine()
        try:
            async with locked_collection_session(a) as session:
                link = relay.links[-1]
                link.arm()
                task = asyncio.create_task(session.execute(text("SELECT 42")))
                await barrier(link.held)

                async def contender():
                    async with locked_collection_session(b):
                        raise AssertionError("Concurrent collector acquired held lock")

                assert isinstance(await failure(contender()), CollectionLockError)
                assert isinstance(await failure(task), CollectionLockError)
                await barrier(link.client_eof)
                assert not await no_lock(), "Local failure is not proof of remote unlock"
                assert isinstance(await failure(contender()), CollectionLockError)
                await link.stop()
                await until(no_lock)
                # The old run is forbidden to reconnect even after ownership becomes available.
                await session.rollback()
                assert isinstance(
                    await failure(session.execute(text("SELECT 1"))), CollectionLockError
                )
                async with locked_collection_session(b) as other:
                    assert await other.scalar(text("SELECT 7")) == 7
                DETAIL.update(
                    stalled_holder_blocks_competitor=True,
                    failed_holder_cannot_resume=True,
                    new_explicit_run_succeeds_after_remote_unlock=True,
                )
        finally:
            await a.dispose()
            await b.dispose()


async def rollback_stall():
    async with Relay(CONFIG["port"]) as relay:
        e = engine(relay.port)
        try:
            async with e.connect() as c:
                await c.execute(text("INSERT INTO gate_ledger VALUES ('rollback-stall', 1)"))
                raw = (await c.get_raw_connection()).driver_connection
                link = relay.links[-1]
                link.arm("up")
                task = asyncio.create_task(c.rollback())
                await barrier(link.held)
                assert outcome_uncertain(await failure(task))
                assert c.invalidated and raw.closed
                assert not await observer("SELECT id FROM gate_ledger WHERE id='rollback-stall'")
                start = time.monotonic()
                await c.close()
                await e.dispose()
                DETAIL["close_dispose_seconds"] = round(time.monotonic() - start, 4)
                assert DETAIL["close_dispose_seconds"] < 0.2
        finally:
            await e.dispose()


async def terminate_cleanup():
    await until(no_lock)
    e = engine()
    try:
        async with locked_collection_session(e) as s:
            pid = await s.scalar(text("SELECT pg_backend_pid()"))
            assert (await observer("SELECT pg_terminate_backend(%s)", (pid,)))[0][0]
            assert isinstance(await failure(s.execute(text("SELECT 1"))), CollectionLockError)
            start = time.monotonic()
            await s.rollback()
            DETAIL["cleanup_seconds"] = round(time.monotonic() - start, 4)
            assert isinstance(await failure(s.commit()), CollectionLockError)
            assert DETAIL["cleanup_seconds"] < 0.2
    finally:
        await e.dispose()
    await until(no_lock)


async def receipt_cancel():
    import httpx

    from github_radar.config import Settings
    from github_radar.github.client import GitHubClient
    from github_radar.services.collection import CollectionService
    from tests.conftest import repo_detail_payload
    from tests.unit.test_collection import plan

    await until(no_lock)
    count = 0
    item = repo_detail_payload(github_id=501, name="deadline-synthetic")

    async def transport(request):
        nonlocal count
        count += 1
        if request.url.path == "/search/repositories":
            return httpx.Response(
                200, json={"items": [item], "total_count": 1, "incomplete_results": False}
            )
        assert request.url.path == "/repos/octo/deadline-synthetic"
        return httpx.Response(200, json=item)

    settings = Settings(  # type: ignore[call-arg]  # runtime-only pydantic-settings option
        _env_file=None,
        github_token="synthetic-only",
        http_max_retries=0,
        rate_limit_pause_threshold=0,
        search_rate_limit_pause_threshold=0,
    )
    async with Relay(CONFIG["port"]) as relay:
        e = engine(relay.port, seconds=15)
        service_box = []
        original = CollectionService._checkpoint

        async def checkpoint(self, *, finished=False):
            if self.report.new_repositories_saved == 1 and not finished:
                event.listen(e.sync_engine, "commit", lambda c: relay.links[-1].arm(), once=True)
            await original(self, finished=finished)

        setattr(CollectionService, "_checkpoint", checkpoint)

        async def worker():
            async with (
                locked_collection_session(e) as s,
                GitHubClient(settings, transport=httpx.MockTransport(transport)) as client,
            ):
                service = CollectionService(client, s)
                service_box.append(service)
                return await service.run("discover", plan=plan(count=1))

        try:
            task = asyncio.create_task(worker())

            async def armed():
                return bool(relay.links and relay.links[-1].held.is_set())

            await until(armed, 3)
            service = service_box[0]
            run_id = service.report.run_id
            rows = await observer(
                "SELECT status, finished_at, report_json, requests_json "
                "FROM collection_runs WHERE id=%s",
                (run_id,),
            )
            assert len(rows) == 1
            status, finished, report, requests = rows[0]
            assert status == "running" and finished is None
            saved = json.loads(report)
            assert (
                saved["new_repositories_saved"] == 1
                and saved["partial"]
                and not saved["duration_is_final"]
            )
            assert json.loads(requests)["total"] == 2
            assert (await observer("SELECT count(*) FROM repositories WHERE github_id=501"))[0][
                0
            ] == 1
            task.cancel()
            exc = await failure(task)
            assert isinstance(exc, CollectionLockError) and outcome_uncertain(exc)
            assert count == 2 and len(service_box) == 1
            after = await observer(
                "SELECT status, finished_at, report_json, requests_json "
                "FROM collection_runs WHERE id=%s",
                (run_id,),
            )
            assert after == rows
            DETAIL.update(
                persisted_repositories=1,
                receipt_status=status,
                partial=True,
                finished_at=None,
                reserved_attempts=2,
                mocked_http_attempts=count,
                whole_run_invocations=1,
                committed_work_not_reported_rolled_back=True,
            )
        finally:
            setattr(CollectionService, "_checkpoint", original)
            await e.dispose()
    await until(no_lock)


@pytest.mark.parametrize(
    "case",
    [
        security_utc,
        negative_security,
        dns_stall,
        long_query,
        statement_timeout,
        cancelled_ownership,
        contender_stall,
        rollback_stall,
        terminate_cleanup,
        receipt_cancel,
    ],
)
async def test_production_deadline_scenarios(case):
    async with asyncio.timeout(22):
        await case()


@pytest.mark.parametrize("seconds", [0.25, 10])
async def test_connect_deadline_and_socket_release(seconds):
    async with asyncio.timeout(12):
        await connect_stall(seconds)


@pytest.mark.parametrize("seconds", [0.3, 5, 15])
async def test_blackholed_server_timeout_reply(seconds):
    async with asyncio.timeout(18):
        await blackhole(seconds)


@pytest.mark.parametrize("direction", ["up", "down"])
async def test_commit_ambiguity_and_reconnect_refusal(direction):
    async with asyncio.timeout(5):
        await commit_fault(direction)


async def native_psycopg_cancellation():
    async with Relay(CONFIG["port"]) as relay:
        c = await psycopg.AsyncConnection.connect(**kwargs(relay.port))
        try:
            await c.execute("SELECT 1")
            link = relay.links[-1]
            link.arm()

            async def query():
                async with asyncio.timeout(0.3):
                    await c.execute("SELECT 2")

            task = asyncio.create_task(query())
            await barrier(link.held)
            exc = await failure(task, minimum=5, maximum=11.3)
            assert isinstance(exc, TimeoutError)
            DETAIL.update(
                requested_outer_deadline_seconds=0.3,
                closed=c.closed,
                native_cancel_drain_extends_caller_deadline=True,
            )
        finally:
            await c.close()


async def close_context_stall():
    async with Relay(CONFIG["port"]) as relay:
        e = engine(relay.port)
        try:
            s = ManagedSession(e)
            await s.execute(text("INSERT INTO gate_ledger VALUES ('context-close', 1)"))
            raw = (await (await s.connection()).get_raw_connection()).driver_connection
            relay.links[-1].arm("up")
            exc = await failure(s.close(), minimum=0.25)
            assert raw is not None and outcome_uncertain(exc) and raw.closed
            assert not await observer("SELECT id FROM gate_ledger WHERE id='context-close'")
            start = time.monotonic()
            await s.close()
            await e.dispose()
            assert time.monotonic() - start < 0.2
            DETAIL["second_cleanup_seconds"] = round(time.monotonic() - start, 4)
        finally:
            await e.dispose()


async def readiness_deadline():
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from github_radar.api.readiness import database_ready

    async with Relay(CONFIG["port"]) as relay:
        e = engine(relay.port, seconds=5, public=True)
        try:
            # Missing schema is an honest readiness failure; no migrations are applied.
            assert await database_ready(async_sessionmaker(e), public=False) is False

            def arm(c, cursor, statement, params, ctx, many):
                if "alembic_version" in statement:
                    relay.links[-1].arm()

            event.listen(e.sync_engine, "before_cursor_execute", arm)
            start = time.monotonic()
            assert await database_ready(async_sessionmaker(e), public=False, timeout=0.25) is False
            elapsed = time.monotonic() - start
            assert 0.2 <= elapsed < 1.3
            DETAIL.update(
                readiness_seconds=round(elapsed, 4),
                limit_seconds=0.25,
                missing_schema_ready=False,
                timeout_ready=False,
            )
            event.remove(e.sync_engine, "before_cursor_execute", arm)
            async with e.connect() as c:
                assert await c.scalar(text("SELECT 1")) == 1
        finally:
            await e.dispose()


async def initial_lock_stall():
    await until(no_lock)
    async with Relay(CONFIG["port"]) as relay:
        e = engine(relay.port)
        try:

            def arm(c, cursor, statement, params, ctx, many):
                if "pg_try_advisory_lock" in statement:
                    relay.links[-1].arm()

            event.listen(e.sync_engine, "before_cursor_execute", arm)
            entered = []

            async def acquire():
                async with locked_collection_session(e):
                    entered.append(True)

            task = asyncio.create_task(acquire())

            async def held():
                return bool(relay.links and relay.links[-1].held.is_set())

            await until(held)
            exc = await failure(task)
            assert outcome_uncertain(exc) and not entered
            DETAIL["guard_body_never_entered"] = True
        finally:
            await e.dispose()
    await until(no_lock)


async def cleanup_unlock_stall():
    await until(no_lock)
    async with Relay(CONFIG["port"]) as relay:
        e = engine(relay.port)
        try:

            async def run():
                async with locked_collection_session(e) as s:
                    await s.execute(text("INSERT INTO gate_ledger VALUES ('cleanup-unlock',1)"))
                    await s.commit()

                    def arm(c, cursor, statement, params, ctx, many):
                        if "pg_advisory_unlock" in statement:
                            relay.links[-1].arm("up")

                    event.listen(e.sync_engine, "before_cursor_execute", arm)

            assert isinstance(await failure(run(), maximum=1.5), CollectionLockError)
            assert (await observer("SELECT count(*) FROM gate_ledger WHERE id='cleanup-unlock'"))[
                0
            ][0] == 1
            DETAIL["prior_commit_preserved"] = True
        finally:
            await e.dispose()
    await until(no_lock)


async def streaming_and_rollback():
    async with Relay(CONFIG["port"]) as relay:
        e = engine(relay.port)
        try:
            async with e.connect() as c:
                result = await c.stream(text("SELECT generate_series(1,100)"))
                assert await result.fetchone() is not None
                relay.links[-1].arm()
                assert outcome_uncertain(await failure(result.fetchall()))
                assert c.invalidated
        finally:
            await e.dispose()


async def normal_partial_cancellation():
    import httpx

    from github_radar.config import Settings
    from github_radar.github.client import GitHubClient
    from github_radar.services.collection import CollectionService

    await until(no_lock)
    started = asyncio.Event()

    async def never_transport(request):
        started.set()
        await asyncio.Event().wait()

    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        github_token="synthetic-only",
        http_max_retries=0,
        rate_limit_pause_threshold=0,
        search_rate_limit_pause_threshold=0,
    )
    from tests.unit.test_collection import plan

    e = engine()
    services = []

    async def run():
        async with (
            locked_collection_session(e) as s,
            GitHubClient(settings, transport=httpx.MockTransport(never_transport)) as client,
        ):
            service = CollectionService(client, s)
            services.append(service)
            await service.run("discover", plan=plan(count=1))

    try:
        task = asyncio.create_task(run())
        await barrier(started)
        task.cancel()
        assert isinstance(await failure(task), asyncio.CancelledError)
        rows = await observer(
            "SELECT status, finished_at, report_json FROM collection_runs WHERE id=%s",
            (services[0].report.run_id,),
        )
        assert rows[0][0] == "partial" and rows[0][1] is not None
        report = json.loads(rows[0][2])
        assert (
            report["stop_reason"] == "interrupted" and report["api_requests_consumed"]["total"] == 1
        )
        assert await no_lock()
        DETAIL.update(receipt_status="partial", final_receipt_confirmed=True, reserved_attempts=1)
    finally:
        await e.dispose()


async def outer_wrapper_deadline():
    await until(no_lock)
    async with Relay(CONFIG["port"]) as relay:
        e = engine(relay.port, seconds=15)
        try:

            async def run():
                async with asyncio.timeout(0.25):
                    async with locked_collection_session(e) as s:
                        await s.execute(text("SELECT 1"))
                        relay.links[-1].arm()
                        await s.execute(text("SELECT 2"))

            start = time.monotonic()
            assert isinstance(await failure(run(), minimum=0.2), CollectionLockError)
            DETAIL.update(
                outer_budget_seconds=0.25,
                io_budget_seconds=15,
                including_cleanup_seconds=round(time.monotonic() - start, 4),
            )
        finally:
            await e.dispose()
    await until(no_lock)


@pytest.mark.parametrize(
    "case",
    [
        close_context_stall,
        readiness_deadline,
        initial_lock_stall,
        cleanup_unlock_stall,
        streaming_and_rollback,
        normal_partial_cancellation,
        outer_wrapper_deadline,
        native_psycopg_cancellation,
    ],
)
async def test_cleanup_readiness_and_negative_control(case):
    async with asyncio.timeout(22):
        await case()


async def test_repeated_cancellation_owns_cleanup_until_connection_abandoned():
    async with Relay(CONFIG["port"]) as relay:
        e = engine(relay.port)
        state = {}
        before = set(asyncio.all_tasks())

        async def run():
            async with e.connect() as c:
                await c.execute(text("INSERT INTO gate_ledger VALUES ('double-cancel',1)"))
                state["raw"] = (await c.get_raw_connection()).driver_connection
                relay.links[-1].arm("up")

        try:
            task = asyncio.create_task(run())

            async def held():
                return bool(relay.links and relay.links[-1].held.is_set())

            await until(held)
            task.cancel()
            task.cancel()
            error = await failure(task, minimum=0.15)
            assert outcome_uncertain(error) and state["raw"].closed
            assert not await observer("SELECT id FROM gate_ledger WHERE id='double-cancel'")
            assert not [
                t
                for t in asyncio.all_tasks() - before
                if getattr(t.get_coro(), "__qualname__", None) == "AsyncConnection.close"
            ]
        finally:
            await e.dispose()


async def collection_deadline():
    import httpx

    from github_radar.config import Settings
    from github_radar.discovery.planning import ExecutionLimits
    from github_radar.github.client import GitHubClient
    from github_radar.services.collection import CollectionService
    from tests.unit.test_collection import plan

    await until(no_lock)
    attempted = asyncio.Event()

    async def waiting_http(request):
        attempted.set()
        await asyncio.Event().wait()

    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        github_token="synthetic-only",
        http_max_retries=0,
        rate_limit_pause_threshold=0,
        search_rate_limit_pause_threshold=0,
    )
    e = engine(seconds=15)
    try:
        async with (
            locked_collection_session(e) as s,
            GitHubClient(settings, transport=httpx.MockTransport(waiting_http)) as client,
        ):
            service = CollectionService(client, s)
            start = time.monotonic()
            report = await service.run(
                "discover", plan=plan(count=1, execution=ExecutionLimits(max_seconds=1))
            )
            elapsed = time.monotonic() - start
            assert attempted.is_set() and 1 <= elapsed < 2
            assert report.status == "partial" and report.stop_reason == "runtime deadline reached"
            rows = await observer(
                "SELECT status,finished_at,requests_json FROM collection_runs WHERE id=%s",
                (report.run_id,),
            )
            assert rows[0][0] == "partial" and rows[0][1] is not None
            assert json.loads(rows[0][2])["total"] == 1
            DETAIL.update(
                budget_seconds=1,
                operation_seconds=round(elapsed, 4),
                receipt_status="partial",
                attempts_reserved=1,
                actual_service_deadline_exercised=True,
            )
    finally:
        await e.dispose()


async def test_actual_collection_deadline_records_confirmed_partial_receipt():
    async with asyncio.timeout(5):
        await collection_deadline()


async def test_public_pool_acquisition_is_bounded_and_failed_session_is_replaced():
    from sqlalchemy.exc import TimeoutError as PoolTimeout

    async with Relay(CONFIG["port"]) as relay:
        e = engine(relay.port, public=True)
        try:
            c1, c2 = await e.connect(), await e.connect()
            try:
                error = await failure(e.connect(), minimum=2.8, maximum=3.8)
                assert isinstance(error, PoolTimeout)
                pid1 = await c1.scalar(text("SELECT pg_backend_pid()"))
                await c1.commit()
                relay.links[0].arm()
                assert outcome_uncertain(await failure(c1.execute(text("SELECT 1"))))
                assert c1.invalidated
            finally:
                await c1.close()
                await c2.close()
            async with e.connect() as connection:
                pid2 = await connection.scalar(text("SELECT pg_backend_pid()"))
                assert pid1 != pid2
            assert e.pool.size() == 2 and e.pool.overflow() == 0
            DETAIL.update(
                pool_size=2, overflow=0, acquisition_budget_seconds=3, recovery_new_backend=True
            )
        finally:
            await e.dispose()
