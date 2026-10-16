"""Receipt-based costs: reservations are measured, future costs are only scenarios."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from typing import Any

from github_radar.analytics.quality import ratio
from github_radar.services.collection import stored_report
from github_radar.storage.models import CollectionRunRow


def collection_stats(receipts: Sequence[CollectionRunRow]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for receipt in sorted(receipts, key=lambda r: (r.started_at, r.id)):
        report = stored_report(receipt)
        duplicate_count = sum(
            op.reason == "duplicate ID" and op.outcome == "skipped" for op in report.operations
        )
        completed = (
            receipt.status == "completed"
            and not report.partial
            and receipt.finished_at is not None
            and report.duration_is_final
        )
        rows.append(
            {
                "run_id": receipt.id,
                "kind": receipt.kind,
                "status": receipt.status,
                "partial": report.partial or not completed,
                "budget_exhausted": report.budget_exhausted,
                "finished": receipt.finished_at is not None,
                "started_at": receipt.started_at.isoformat(),
                "duration_seconds": report.duration_seconds,
                "duration_is_final": report.duration_is_final and receipt.finished_at is not None,
                "candidates_examined": report.candidates_examined,
                "duplicate_candidates": duplicate_count,
                "duplicate_rate": ratio(duplicate_count, report.candidates_examined),
                "new_repositories": report.new_repositories_saved,
                "existing_repositories_reused": report.existing_repositories_reused,
                "repositories_refreshed": report.repositories_refreshed,
                "profiles_fetched": report.contributor_profiles_fetched,
                "profiles_reused": report.contributor_profiles_reused,
                "requests": report.api_requests_consumed,
                "api_attempts_per_new_repository": ratio(
                    report.api_requests_consumed.get("total", 0), report.new_repositories_saved
                )
                if receipt.kind == "discover"
                else None,
            }
        )
    requests: Counter[str] = Counter()
    for row in rows:
        requests.update(row["requests"])
    discovery = [r for r in rows if r["kind"] == "discover"]
    completed_runs = [r for r in discovery if not r["partial"] and r["duration_is_final"]]
    new_count = sum(r["new_repositories"] for r in discovery)
    discovery_attempts = sum(r["requests"].get("total", 0) for r in discovery)
    candidates = sum(r["candidates_examined"] for r in discovery)
    duplicates = sum(r["duplicate_candidates"] for r in discovery)
    measured_new = sum(r["new_repositories"] for r in completed_runs)
    per_new = ratio(sum(r["requests"].get("total", 0) for r in completed_runs), measured_new)
    projections = []
    for target in (50, 100, 300, 500):
        # Explicit planning scenario: 50% candidate headroom over 12 balanced cohorts.
        candidate_cap = math.ceil(target * 1.5)
        slots = [candidate_cap // 12 + int(i < candidate_cap % 12) for i in range(12)]
        search_pages = sum(math.ceil(count / 100) for count in slots)
        projections.append(
            {
                "target_new_repositories": target,
                "measured_ratio_estimate_attempts": math.ceil(target * per_new)
                if per_new is not None
                else None,
                "scenario_candidate_cap": candidate_cap,
                "scenario_new_repository_cap": target,
                "scenario_search_attempt_cap": search_pages * 3,
                "scenario_detail_attempt_cap": candidate_cap * 3,
                "scenario_http_attempt_cap": (search_pages + candidate_cap) * 3,
                "scenario_contributor_profile_attempt_cap": 0,
            }
        )
    return {
        "schema_version": 1,
        "mode": "read-only",
        "request_accounting": "durable reservations before transport; crash may overcount sends",
        "run_count": len(rows),
        "requests": dict(sorted(requests.items())),
        "status_counts": dict(sorted(Counter(r["status"] for r in rows).items())),
        "partial_or_unfinished_runs": sum(r["partial"] for r in rows),
        "partial_or_unfinished_frequency": ratio(sum(r["partial"] for r in rows), len(rows)),
        "budget_exhausted_runs": sum(r["budget_exhausted"] for r in rows),
        "budget_exhaustion_frequency": ratio(sum(r["budget_exhausted"] for r in rows), len(rows)),
        "discovery": {
            "runs": len(discovery),
            "new_repositories": new_count,
            "api_attempts_per_new_repository": ratio(discovery_attempts, new_count),
            "candidates_examined": candidates,
            "duplicate_candidates": duplicates,
            "duplicate_candidate_rate": ratio(duplicates, candidates),
            "search_attempts": sum(r["requests"].get("search", 0) for r in discovery),
            "detail_attempts": sum(r["requests"].get("repository_details", 0) for r in discovery),
        },
        "enrichment": {
            "contributor_attempts": requests["contributors"],
            "profile_attempts": requests["profiles"],
            "profiles_fetched": sum(r["profiles_fetched"] for r in rows),
            "profiles_reused": sum(r["profiles_reused"] for r in rows),
            "measured": any(r["kind"] == "enrich" for r in rows),
            "note": "Zero calls without an enrichment run do not estimate future enrichment cost.",
        },
        "projection_basis": {
            "completed_discovery_runs": len(completed_runs),
            "new_repositories": measured_new,
            "attempts_per_new_repository": per_new,
            "statistically_reliable": False,
            "note": "Provisional linear extrapolation of completed discovery only; "
            "query overlap, existing IDs, pagination and retries can change yield. "
            "No guaranteed unique repository count or confidence interval.",
            "upper_bound_scenario": "12 balanced cohorts, 100 items/page, 1.5 candidates "
            "per target, at most 3 attempts per page/detail, no enrichment. "
            "Caps apply only if explicitly configured; not a yield bound.",
        },
        "projections": projections,
        "runs": rows,
    }
