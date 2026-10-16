"""Pure discovery plans: local configuration in, bounded search cohorts out."""

from __future__ import annotations

import tomllib
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from github_radar.config.settings import GitHubRequestLimits
from github_radar.github.repositories import build_search_query

Name = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]*$", max_length=64)]
Count = Annotated[int, Field(ge=0, strict=True)]
Days = Annotated[int, Field(ge=1, le=36500, strict=True)]
SearchSort = Literal["stars", "forks", "updated", "help-wanted-issues", "best-match"]


class PlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ExecutionLimits(PlanModel):
    max_candidates: int = Field(default=100, ge=1, le=10000, strict=True)
    max_new_repositories: int = Field(default=30, ge=0, le=1000, strict=True)
    max_repositories: int = Field(default=25, ge=1, le=1000, strict=True)
    max_seconds: int = Field(default=120, ge=1, le=3600, strict=True)


class DiscoveryCategory(PlanModel):
    name: Name
    query: str = Field(min_length=1)


class DiscoveryRecipe(PlanModel):
    name: Name
    min_stars: Count = 0
    max_stars: Count | None = None
    created_within_days: Days | None = None
    pushed_within_days: Days | None = None
    sort: SearchSort = "stars"
    order: Literal["asc", "desc"] = "desc"

    @model_validator(mode="after")
    def check_star_range(self) -> Self:
        if self.max_stars is not None and self.max_stars < self.min_stars:
            raise ValueError("max_stars must be >= min_stars")
        return self


class DiscoveryConfig(PlanModel):
    categories: tuple[DiscoveryCategory, ...] = Field(min_length=1)
    recipes: tuple[DiscoveryRecipe, ...] = Field(min_length=1)
    limit_per_cohort: int = Field(default=3, ge=1, le=1000, strict=True)
    per_page: int = Field(default=100, ge=1, le=100, strict=True)
    contributors_limit: int = Field(default=30, ge=0, le=1000, strict=True)
    budget: GitHubRequestLimits = Field(default_factory=GitHubRequestLimits)
    execution: ExecutionLimits = Field(default_factory=ExecutionLimits)

    @model_validator(mode="after")
    def check_names(self) -> Self:
        for group in (self.categories, self.recipes):
            names = [entry.name for entry in group]
            if len(names) != len(set(names)):
                raise ValueError("Category and recipe names must be unique within each group")
        return self


class PlannedCohort(PlanModel):
    category: Name
    recipe: Name
    query: str = Field(min_length=1)
    sort: SearchSort
    order: Literal["asc", "desc"]
    limit: int = Field(ge=1, le=1000, strict=True)


class DiscoveryPlan(PlanModel):
    mode: Literal["local-only"] = "local-only"
    as_of: date
    cohorts: tuple[PlannedCohort, ...]
    per_page: int = Field(ge=1, le=100, strict=True)
    contributors_limit: int = Field(ge=0, le=1000, strict=True)
    candidate_slots: int
    search_requests_without_retries: int
    all_new_enrichment_requests_without_retries: dict[str, int]
    budget: GitHubRequestLimits
    execution: ExecutionLimits = Field(default_factory=ExecutionLimits)
    warnings: tuple[str, ...]


def load_discovery_config(path: Path) -> DiscoveryConfig:
    with path.open("rb") as source:
        return DiscoveryConfig.model_validate(tomllib.load(source))


def make_plan(
    cohorts: tuple[PlannedCohort, ...],
    *,
    as_of: date,
    per_page: int,
    contributors_limit: int,
    budget: GitHubRequestLimits,
    execution: ExecutionLimits | None = None,
) -> DiscoveryPlan:
    """Count pages and expose worst-case demand, without looking up repositories."""
    if not cohorts:
        raise ValueError("Select at least one cohort")
    if not 1 <= per_page <= 100 or contributors_limit < 0:
        raise ValueError("per_page must be 1..100 and contributors_limit must be >= 0")
    keys = [(c.query, c.sort, c.order) for c in cohorts]
    if len(set(keys)) != len(keys):
        raise ValueError("Duplicate effective search cohorts; use distinct queries or recipes")
    search = sum((c.limit + per_page - 1) // per_page for c in cohorts)
    if search > min(budget.search, budget.total):
        raise ValueError(
            f"Plan needs {search} search requests before retries; "
            f"budget allows search={budget.search}, total={budget.total}"
        )
    candidates = sum(c.limit for c in cohorts)
    demand = {
        "repository_details": candidates,
        "contributors": candidates * ((contributors_limit + 99) // 100),
        "profiles": candidates * contributors_limit,
    }
    warnings = [
        "Candidate slots are not unique repositories or guaranteed results; "
        "overlap, availability and existing database coverage are unknown.",
        "Retries consume the same caps; GitHub rate limits may stop a run earlier.",
    ]
    for scope, required in {**demand, "total": search + sum(demand.values())}.items():
        cap = getattr(budget, scope)
        if required > cap:
            warnings.append(
                f"{scope}: all-new demand {required} exceeds cap {cap}; "
                "full enrichment is not budgeted."
            )
    return DiscoveryPlan(
        as_of=as_of,
        cohorts=cohorts,
        per_page=per_page,
        contributors_limit=contributors_limit,
        candidate_slots=candidates,
        search_requests_without_retries=search,
        all_new_enrichment_requests_without_retries=demand,
        budget=budget,
        execution=execution or ExecutionLimits(),
        warnings=tuple(warnings),
    )


def plan_discovery(
    config: DiscoveryConfig,
    *,
    as_of: date,
    categories: tuple[str, ...] = (),
    recipes: tuple[str, ...] = (),
) -> DiscoveryPlan:
    """Cross selected categories with recipes, retaining equal per-cohort quotas."""
    for selected, available in (
        (categories, {c.name for c in config.categories}),
        (recipes, {r.name for r in config.recipes}),
    ):
        unknown = set(selected) - available
        if unknown:
            raise ValueError(f"Unknown selection: {', '.join(sorted(unknown))}")
    cohorts = []
    for recipe in config.recipes:
        if recipes and recipe.name not in recipes:
            continue
        try:
            created_after = (
                as_of - timedelta(days=recipe.created_within_days)
                if recipe.created_within_days
                else None
            )
            pushed_after = (
                as_of - timedelta(days=recipe.pushed_within_days)
                if recipe.pushed_within_days
                else None
            )
        except OverflowError as exc:
            raise ValueError("Recipe date window precedes year 1; choose a later --as-of") from exc
        for category in config.categories:
            if categories and category.name not in categories:
                continue
            query = build_search_query(
                query=f"{category.query} is:public archived:false fork:false",
                min_stars=recipe.min_stars if recipe.max_stars is None else None,
                created_after=created_after,
            )
            if recipe.max_stars is not None:
                query += f" stars:{recipe.min_stars}..{recipe.max_stars}"
            if pushed_after:
                query += f" pushed:>={pushed_after.isoformat()}"
            cohorts.append(
                PlannedCohort(
                    category=category.name,
                    recipe=recipe.name,
                    query=query,
                    sort=recipe.sort,
                    order=recipe.order,
                    limit=config.limit_per_cohort,
                )
            )
    return make_plan(
        tuple(cohorts),
        as_of=as_of,
        per_page=config.per_page,
        contributors_limit=config.contributors_limit,
        budget=config.budget,
        execution=config.execution,
    )


def load_reviewed_plan(path: Path) -> DiscoveryPlan:
    """Reject edited derived totals rather than trusting an arbitrary JSON plan."""
    plan = DiscoveryPlan.model_validate_json(path.read_text(encoding="utf-8"))
    verified = make_plan(
        plan.cohorts, as_of=plan.as_of, per_page=plan.per_page,
        contributors_limit=plan.contributors_limit, budget=plan.budget,
        execution=plan.execution,
    )
    if plan != verified:
        raise ValueError("Plan totals or warnings differ; regenerate and review the plan")
    return verified
