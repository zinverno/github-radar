import copy
import io
import json
import stat
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

from verify import digest, read_archive, timestamp, verify_approval, verify_provenance

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

    def test_artifact_approval_never_enables_npm_exceptions(self):
        with self.assertRaisesRegex(ValueError, "UNAPPROVED"):
            verify_approval(POLICY, NOW)
        p = copy.deepcopy(POLICY)
        p["approval"] = {
            "approver": "test-operator",
            "approved_at": "2026-11-04T01:00:00Z",
            "record": "https://github.com/zinverno/github-radar/pull/7#issuecomment-123",
            "artifact_sha256": p["candidate"]["sha256"],
            "accepted_historical_risks": p["historical_risks"],
        }
        verify_approval(p, NOW)
        for field, value in [
            ("approver", ""),
            ("record", ""),
            ("artifact_sha256", "other"),
            ("accepted_historical_risks", []),
            ("approved_at", "2026-11-05T00:00:00Z"),
        ]:
            modified = copy.deepcopy(p)
            modified["approval"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify_approval(modified, NOW)
        with self.assertRaisesRegex(ValueError, "expired"):
            verify_approval(p, timestamp(p["candidate"]["expires_at"]))


if __name__ == "__main__":
    unittest.main()
