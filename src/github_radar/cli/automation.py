"""Fail-closed automation entry points. Dry runs never load credentials or connect."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import threading
from enum import StrEnum
from typing import Any

import typer
from sqlalchemy import text

from github_radar.services.automation import (
    REFRESH_BUDGET,
    AutomationBlocked,
    CollectorCredentials,
    Kind,
    acknowledged_runs,
    activation_reasons,
    check_collector_role,
    discovery_plan,
    operator_report,
    preview,
    run_scheduled,
)
from github_radar.storage.collection import CollectionLockError
from github_radar.storage.db import ManagedSession
from github_radar.storage.deadlines import outcome_uncertain
from github_radar.util import utcnow

app = typer.Typer(help="Disabled-by-default bounded collection scheduling.", no_args_is_help=True)


class Job(StrEnum):
    refresh = "refresh"
    discover = "discover"


def emit(result: dict[str, Any]) -> None:
    typer.echo(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


@app.command("run")
def run(
    job: Job = typer.Argument(...),
    live: bool = typer.Option(
        False, "--live", help="Requires all activation and credential gates."
    ),
) -> None:
    """Preview by default; live execution is restricted to trusted main Actions jobs."""
    kind: Kind = "refresh" if job == Job.refresh else "discover"
    try:
        now = utcnow()
        proposal = preview(kind, now)
        if not live:
            proposal["live_blockers"] = activation_reasons(kind, os.environ)
            emit(proposal)
            return
        reasons = activation_reasons(kind, os.environ)
        if reasons:
            emit({"status": "blocked", "reasons": reasons})
            raise typer.Exit(2)
        plan = None
        if kind == "discover":
            plan, digest = discovery_plan(now)
            if digest != os.environ.get("RADAR_DISCOVERY_PLAN_SHA256"):
                raise AutomationBlocked("Discovery catalog digest does not match operator approval")
        credentials = CollectorCredentials.from_environment(os.environ)
        acknowledged = acknowledged_runs(os.environ.get("COLLECTOR_ACK_UNFINISHED_RUNS", ""))

        async def execute() -> dict[str, Any]:
            engine = credentials.engine()
            task = asyncio.current_task()
            assert task is not None
            loop = asyncio.get_running_loop()
            signals = threading.current_thread() is threading.main_thread()
            if signals:
                loop.add_signal_handler(signal.SIGTERM, task.cancel)
            try:
                async with asyncio.timeout(330):
                    return await run_scheduled(
                        engine,
                        credentials.settings(plan.budget if plan else REFRESH_BUDGET),
                        kind,
                        plan=plan,
                        acknowledged=acknowledged,
                    )
            finally:
                if signals:
                    loop.remove_signal_handler(signal.SIGTERM)
                await engine.dispose()

        result = asyncio.run(execute())
        if plan is not None:
            result["approved_catalog_sha256"] = digest
        emit(result)
        if result["status"] == "partial":
            raise typer.Exit(1)
    except typer.Exit:
        raise
    except AutomationBlocked as exc:
        emit({"status": "blocked", "reason": str(exc)})
        raise typer.Exit(2) from None
    except CollectionLockError as exc:
        emit(
            {
                "status": "blocked",
                "database_outcome": (
                    "uncertain" if exc.database_outcome_uncertain else "not_assessed"
                ),
                "reason": "Collection lock unavailable or ownership lost; "
                "inspect receipts before any new run",
            }
        )
        raise typer.Exit(1) from None
    except (asyncio.CancelledError, KeyboardInterrupt):
        emit({"status": "interrupted", "reason": "Inspect receipts; no automatic retry"})
        raise typer.Exit(130) from None
    except Exception as exc:
        # Never render DSNs, tokens, SQL parameters, remote exception messages or tracebacks.
        emit(
            {
                "status": "failed",
                "database_outcome": "uncertain" if outcome_uncertain(exc) else "not_assessed",
                "reason": "Invalid plan, database/configuration failure or "
                "deadline exceeded; inspect receipts and operator diagnostics",
            }
        )
        raise typer.Exit(1) from None


@app.command("status")
def status() -> None:
    """Read dataset/receipt diagnostics using only the dedicated collector connection."""

    async def read() -> dict[str, Any]:
        credentials = CollectorCredentials.from_environment(os.environ)
        engine = credentials.engine()
        try:
            async with asyncio.timeout(60), engine.connect() as connection, connection.begin():
                await connection.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                )
                async with ManagedSession(bind=connection) as session:
                    await check_collector_role(session)
                    result = await operator_report(session, utcnow())
                    result["activation_blockers"] = {
                        kind: activation_reasons(kind, os.environ)
                        for kind in ("refresh", "discover")
                    }
                    _, digest = discovery_plan(utcnow())
                    result["discovery_catalog_sha256"] = digest
                    approved = os.environ.get("RADAR_DISCOVERY_PLAN_SHA256")
                    if approved and digest != approved:
                        result["activation_blockers"]["discover"].append(
                            "Discovery catalog digest does not match operator approval"
                        )
                    acknowledged = acknowledged_runs(
                        os.environ.get("COLLECTOR_ACK_UNFINISHED_RUNS", "")
                    )
                    unfinished = set(result["unfinished_runs"])
                    result["acknowledged_unfinished_runs"] = sorted(unfinished & acknowledged)
                    if unfinished - acknowledged:
                        result["database_blockers"] = [
                            "Unfinished receipts require operator review"
                        ]
                    return result
        finally:
            await engine.dispose()

    try:
        emit(asyncio.run(read()))
    except Exception:
        emit(
            {
                "status": "blocked",
                "reason": "Cannot read operator diagnostics; check dedicated "
                "role, verified TLS, database availability and schema 0006",
            }
        )
        raise typer.Exit(1) from None
