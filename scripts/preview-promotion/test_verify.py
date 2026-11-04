import copy
import io
import json
import stat
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from verify import (
    approval_comment_body,
    digest,
    read_archive,
    timestamp,
    verify_approval,
    verify_provenance,
)

POLICY = json.loads(Path("docs/preview-promotion.json").read_text())
NOW = timestamp("2026-11-04T12:00:00Z")


def provenance():
    p = POLICY
    repo = {"id": p["repository_id"], "full_name": p["repository"]}
    run = {
        "id": p["run_id"],
        "run_attempt": 1,
        "workflow_id": p["workflow_id"],
        "path": p["workflow_path"],
        "event": "pull_request",
        "status": "completed",
        "conclusion": "failure",
        "head_sha": p["source_head"],
        "head_branch": "feat/static-preview",
        "repository": repo,
        "head_repository": repo.copy(),
        "pull_requests": [
            {"number": 6, "head": {"sha": p["source_head"]}, "base": {"sha": p["source_base"]}}
        ],
        "head_commit": {"id": p["source_head"], "tree_id": "same-tree"},
    }
    merge = {
        "sha": p["source_checkout"],
        "parents": [{"sha": p["source_base"]}, {"sha": p["source_head"]}],
        "tree": {"sha": "same-tree"},
    }
    jobs = {
        "total_count": 1,
        "jobs": [
            {
                "id": p["source_job_id"],
                "run_id": p["run_id"],
                "conclusion": "failure",
                "steps": [
                    {"name": name, "conclusion": result}
                    for name, result in p["source_steps"].items()
                ],
            }
        ],
    }
    artifacts = {
        kind: {
            "id": p[kind]["id"],
            "name": p[kind]["name"],
            "size_in_bytes": p[kind]["size"],
            "digest": "sha256:" + p[kind]["sha256"],
            "expired": False,
            "expires_at": p[kind]["expires_at"],
            "workflow_run": {
                "id": p["run_id"],
                "head_sha": p["source_head"],
                "repository_id": p["repository_id"],
                "head_repository_id": p["repository_id"],
                "head_branch": "feat/static-preview",
            },
        }
        for kind in ("candidate", "evidence")
    }
    return run, merge, jobs, artifacts


class PromotionTests(unittest.TestCase):
    def test_provenance_and_rejections(self):
        verify_provenance(POLICY, *provenance(), NOW)
        linked_pr_changed = provenance()
        linked_pr_changed[0]["pull_requests"][0]["head"]["sha"] = "later-pr-head"
        linked_pr_changed[0]["pull_requests"][0]["base"]["sha"] = "later-main"
        verify_provenance(POLICY, *linked_pr_changed, NOW)
        cases = [
            (0, ("head_sha",), "other"),
            (0, ("run_attempt",), 2),
            (0, ("workflow_id",), 1),
            (0, ("path",), ".github/workflows/untrusted.yml"),
            (0, ("repository", "id"), 1),
            (0, ("head_repository", "id"), 1),
            (0, ("status",), "in_progress"),
            (0, ("conclusion",), "success"),
            (1, ("tree", "sha"), "changed"),
            (1, ("parents", 1, "sha"), "changed"),
            (2, ("jobs", 0, "steps", 0, "conclusion"), "failure"),
            (3, ("candidate", "id"), 1),
            (3, ("candidate", "expired"), True),
            (3, ("candidate", "digest"), "sha256:changed"),
            (3, ("evidence", "digest"), "sha256:changed"),
            (3, ("candidate", "workflow_run", "head_repository_id"), 1),
        ]
        for index, path, value in cases:
            with self.subTest(path=path):
                data = copy.deepcopy(provenance())
                target = data[index]
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                with self.assertRaises(ValueError):
                    verify_provenance(POLICY, *data, NOW)
        with self.assertRaisesRegex(ValueError, "expired"):
            verify_provenance(POLICY, *provenance(), timestamp(POLICY["candidate"]["expires_at"]))

    def test_archive_validation_before_extraction(self):
        files = {"index.html": {"bytes": 4, "sha256": digest(b"demo")}}

        def check(entries, *, corrupt_hash=False):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                for name, mode, data in entries:
                    info = zipfile.ZipInfo(name)
                    info.external_attr = mode << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", UserWarning)
                        archive.writestr(info, data)
            data = buffer.getvalue()
            expected = {"size": len(data), "sha256": "wrong" if corrupt_hash else digest(data)}
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "artifact.zip"
                path.write_bytes(data)
                return read_archive(path, expected, files)

        regular = ("index.html", stat.S_IFREG | 0o644, b"demo")
        self.assertEqual(check([regular]), {"index.html": b"demo"})
        cases = [
            [regular, regular],
            [regular, ("extra.js", stat.S_IFREG, b"extra")],
            [("../index.html", stat.S_IFREG, b"demo")],
            [("/index.html", stat.S_IFREG, b"demo")],
            [("assets\\index.html", stat.S_IFREG, b"demo")],
            [("index.html.map", stat.S_IFREG, b"demo")],
            [("index.html", stat.S_IFLNK, b"demo")],
            [("index.html", stat.S_IFIFO, b"demo")],
            [("index.html", stat.S_IFREG, b"evil")],
            [("index.html", stat.S_IFREG, b"too long")],
        ]
        for entries in cases:
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                check(entries)
        with self.assertRaisesRegex(ValueError, "Archive hash"):
            check([regular], corrupt_hash=True)


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.policy = copy.deepcopy(POLICY)
        self.policy["approval"] = {
            "approver": "test-operator",
            "approved_at": "2026-11-04T01:00:00Z",
            "record": "https://github.com/zinverno/github-radar/pull/7#issuecomment-123",
            "artifact_sha256": POLICY["candidate"]["sha256"],
            "accepted_historical_risks": POLICY["historical_risks"],
        }
        self.url = "https://api.github.com/repos/zinverno/github-radar/issues/comments/123"
        self.comment = {
            "id": 123,
            "url": self.url,
            "html_url": self.policy["approval"]["record"],
            "issue_url": "https://api.github.com/repos/zinverno/github-radar/issues/7",
            "user": {"type": "User", "login": "test-operator"},
            "created_at": "2026-11-04T01:00:00Z",
            "updated_at": "2026-11-04T01:00:00Z",
            "body": approval_comment_body(POLICY),
        }
        self.response = MagicMock(status=200)
        self.response.geturl.return_value = self.url
        self.response.read.side_effect = lambda: json.dumps(self.comment).encode()
        fetch = patch("verify.urlopen")
        self.fetch = fetch.start()
        self.addCleanup(fetch.stop)
        self.fetch.return_value.__enter__.return_value = self.response

    def test_independently_fetches_exact_operator_comment_without_credentials(self):
        verify_approval(self.policy, NOW)
        self.fetch.assert_called_once()
        args, kwargs = self.fetch.call_args
        self.assertEqual(args[0].full_url, self.url)
        self.assertEqual(args[0].get_method(), "GET")
        self.assertEqual(args[0].get_header("X-github-api-version"), "2022-11-28")
        self.assertFalse(args[0].has_header("Authorization"))
        self.assertEqual(kwargs, {"timeout": 20})
        self.assertIsNone(POLICY["approval"])
        documented = Path("docs/PREVIEW_PROMOTION.md").read_text().split("```text\n", 1)[1]
        self.assertEqual(self.comment["body"], documented.split("\n```", 1)[0])

    def test_allows_only_line_ending_and_login_case_normalization(self):
        self.comment["body"] = "\r\n" + self.comment["body"].replace("\n", "\r\n") + "\r\n"
        self.comment["user"]["login"] = "Test-Operator"
        verify_approval(self.policy, NOW)

    def test_unapproved_or_invalid_record_never_fetches(self):
        with self.assertRaisesRegex(ValueError, "UNAPPROVED"):
            verify_approval(POLICY, NOW)
        for field, value in [
            ("approver", ""),
            ("record", ""),
            ("record", "https://github.com/zinverno/github-radar/pull/6#issuecomment-123"),
            ("record", "https://github.com/zinverno/github-radar/pull/7#pullrequestreview-123"),
            ("record", "https://github.com/other/repository/pull/7#issuecomment-123"),
            ("artifact_sha256", "other"),
            ("accepted_historical_risks", []),
            ("approved_at", "2026-11-05T00:00:00Z"),
        ]:
            modified = copy.deepcopy(self.policy)
            modified["approval"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                verify_approval(modified, NOW)
        with self.assertRaisesRegex(ValueError, "expired"):
            verify_approval(self.policy, timestamp(POLICY["candidate"]["expires_at"]))
        self.fetch.assert_not_called()

    def test_rejects_foreign_missing_or_edited_comment_fields(self):
        original = copy.deepcopy(self.comment)
        cases = [
            ("id", 124),
            ("url", self.url + "4"),
            ("html_url", original["html_url"].replace("pull/7", "pull/6")),
            ("issue_url", original["issue_url"].replace("issues/7", "issues/6")),
            ("user", {"type": "User", "login": "another-operator"}),
            ("user", {"type": "Bot", "login": "test-operator"}),
            ("user", None),
            ("created_at", "2026-11-04T02:00:00Z"),
            ("updated_at", "2026-11-04T02:00:00Z"),
        ]
        for field, value in cases + [(field, None) for field in original]:
            self.comment = {**original, field: value}
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                verify_approval(self.policy, NOW)

    def test_rejects_omitted_changed_quoted_or_revoked_acceptance(self):
        body = self.comment["body"]
        omitted = ["13256888181", POLICY["candidate"]["sha256"], *POLICY["historical_risks"]]
        cases = [body.replace(text, "") for text in omitted] + [
            body.replace("13256888181", "13256888182"),
            body.replace(POLICY["candidate"]["sha256"], "0" * 64),
            body.replace("I approve", "I do not approve"),
            body.replace("I accept", "I do not accept"),
            "> " + body.replace("\n", "\n> "),
            body + "\nI revoke this approval.",
        ]
        for value in cases:
            self.comment["body"] = value
            with self.subTest(body=value), self.assertRaisesRegex(ValueError, "explicitly accept"):
                verify_approval(self.policy, NOW)

    def test_missing_deleted_unavailable_or_malformed_api_response_fails_closed(self):
        for error in [
            HTTPError(self.url, 404, "Not Found", {}, io.BytesIO()),
            HTTPError(self.url, 403, "Forbidden", {}, io.BytesIO()),
            URLError("unavailable"),
            TimeoutError("timeout"),
        ]:
            self.fetch.side_effect = error
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, "fetch"):
                verify_approval(self.policy, NOW)
            if isinstance(error, HTTPError):
                error.close()
        self.fetch.side_effect = None
        self.response.read.side_effect = None
        for response in [b"null", b"{}", b"[]", b"not JSON"]:
            self.response.read.return_value = response
            with self.subTest(response=response), self.assertRaises(ValueError):
                verify_approval(self.policy, NOW)

    def test_redirected_or_unsuccessful_response_fails_closed(self):
        self.response.geturl.return_value = "https://example.com/comment"
        with self.assertRaisesRegex(ValueError, "fetch"):
            verify_approval(self.policy, NOW)
        self.response.geturl.return_value = self.url
        self.response.status = 503
        with self.assertRaisesRegex(ValueError, "fetch"):
            verify_approval(self.policy, NOW)


if __name__ == "__main__":
    unittest.main()
