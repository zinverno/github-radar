"""Receipt accounting and explicitly provisional scaling scenarios."""

import json
from datetime import UTC, datetime

import pytest

from github_radar.services.collection import CollectionReport, Operation
from github_radar.services.collection_diagnostics import collection_stats
from github_radar.storage.models import CollectionRunRow

NOW = datetime(2026, 10, 16, 18, tzinfo=UTC)


def receipt(
    identity="pilot",
    *,
    kind="discover",
    status="completed",
    new=5,
    candidates=6,
    requests=None,
    exhausted=False,
    duration=3.805,
):
    used = requests or {
        "total": 7,
        "search": 2,
        "repository_details": 5,
        "contributors": 0,
        "profiles": 0,
    }
    report = CollectionReport(
        run_id=identity,
        kind=kind,
        status=status,
        partial=status != "completed",
        duration_seconds=duration,
        duration_is_final=status != "running",
        candidates_examined=candidates,
        new_repositories_saved=new,
        api_requests_consumed=used,
        budget_exhausted=exhausted,
        operations=[
            Operation(
                kind="repository", target="duplicate", outcome="skipped", reason="duplicate ID"
            )
        ]
        if candidates
        else [],
    )
    return CollectionRunRow(
        id=identity,
        kind=kind,
        status=status,
        started_at=NOW,
        finished_at=None if status == "running" else NOW,
        input_json="{}",
        report_json=report.model_dump_json(),
        requests_json=json.dumps(used),
    )


def test_pilot_measured_values_and_projections_are_distinct():
    result = collection_stats([receipt()])
    assert result["discovery"]["api_attempts_per_new_repository"] == 1.4
    assert result["discovery"]["duplicate_candidate_rate"] == pytest.approx(1 / 6)
    assert [p["measured_ratio_estimate_attempts"] for p in result["projections"]] == [
        70,
        140,
        420,
        700,
    ]
    assert not result["projection_basis"]["statistically_reliable"]
    assert not result["enrichment"]["measured"]
    assert result["runs"][0]["duration_seconds"] == 3.805
    assert [p["scenario_http_attempt_cap"] for p in result["projections"]] == [261, 486, 1386, 2286]


def test_partial_and_unfinished_costs_not_labeled_completed_or_used_for_yield_projection():
    partial = receipt("partial", status="partial", new=1, exhausted=True)
    interrupted = receipt("interrupted", status="running", new=0, candidates=0)
    # Reservations can be newer than the last result checkpoint.
    interrupted.requests_json = json.dumps({"total": 12, "search": 2, "repository_details": 10})
    result = collection_stats([receipt(), partial, interrupted])
    assert result["requests"]["total"] == 26
    assert result["partial_or_unfinished_frequency"] == pytest.approx(2 / 3)
    assert result["budget_exhaustion_frequency"] == pytest.approx(1 / 3)
    assert result["projection_basis"]["completed_discovery_runs"] == 1
    assert result["projection_basis"]["attempts_per_new_repository"] == 1.4
    assert not result["runs"][0]["duration_is_final"]


def test_empty_and_zero_yield_do_not_invent_cost_or_success():
    empty = collection_stats([])
    assert empty["partial_or_unfinished_frequency"] is None
    assert empty["discovery"]["api_attempts_per_new_repository"] is None
    assert all(p["measured_ratio_estimate_attempts"] is None for p in empty["projections"])
    zero = collection_stats([receipt(new=0)])
    assert zero["projection_basis"]["attempts_per_new_repository"] is None
    zero_cost = collection_stats([receipt(requests={"total": 0})])
    assert all(p["measured_ratio_estimate_attempts"] == 0 for p in zero_cost["projections"])


def test_enrichment_and_refresh_do_not_pollute_discovery_ratio():
    enrichment = receipt(
        "enrich",
        kind="enrich",
        new=0,
        candidates=0,
        requests={"total": 8, "contributors": 2, "profiles": 6},
    )
    refresh = receipt(
        "refresh",
        kind="refresh",
        new=0,
        candidates=0,
        requests={"total": 5, "repository_details": 5},
    )
    result = collection_stats([receipt(), enrichment, refresh])
    assert result["discovery"]["api_attempts_per_new_repository"] == 1.4
    assert result["enrichment"]["measured"]
    assert result["enrichment"]["profile_attempts"] == 6
    assert result["requests"]["total"] == 20
