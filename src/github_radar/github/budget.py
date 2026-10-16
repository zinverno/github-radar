"""Per-run HTTP attempt accounting; failures and retries consume budget too."""

from __future__ import annotations

from github_radar.config.settings import GitHubRequestLimits
from github_radar.github.errors import GitHubError


class RequestBudgetExceeded(GitHubError):
    """A request was refused before sending because a run cap was reached."""

    def __init__(self, scope: str, limit: int, used: dict[str, int]) -> None:
        self.scope = scope
        self.limit = limit
        self.used = dict(used)
        super().__init__(f"GitHub request budget exhausted: {scope} ({used[scope]}/{limit}).")


class RequestBudget:
    """Share one instance across all clients participating in the same run."""

    def __init__(self, limits: GitHubRequestLimits) -> None:
        self.limits = limits
        self._used = dict.fromkeys(limits.model_dump(), 0)

    @property
    def used(self) -> dict[str, int]:
        return dict(self._used)

    def consume(self, path: str) -> None:
        """Reserve before the transport await; never refund an attempted call."""
        parts = path.strip("/").split("/")
        scope = None
        if parts[0] == "search":
            scope = "search"
        elif parts[0] == "repos" and len(parts) == 3:
            scope = "repository_details"
        elif parts[0] == "repos" and len(parts) == 4 and parts[3] == "contributors":
            scope = "contributors"
        elif parts[0] == "users" and len(parts) == 2:
            scope = "profiles"
        scopes = ["total", scope] if scope else ["total"]
        for name in scopes:
            limit = getattr(self.limits, name)
            if self._used[name] >= limit:
                raise RequestBudgetExceeded(name, limit, self._used)
        for name in scopes:
            self._used[name] += 1
