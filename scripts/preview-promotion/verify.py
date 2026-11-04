"""Verify one existing preview; never build, execute, or substitute downloaded code."""

import argparse
import hashlib
import json
import re
import stat
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.error import URLError
from urllib.request import Request, urlopen


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def verify_provenance(policy, run, merge, jobs, artifacts, now):
    require(run["id"] == policy["run_id"] and run["run_attempt"] == 1, "Wrong run/attempt")
    require(
        run["workflow_id"] == policy["workflow_id"] and run["path"] == policy["workflow_path"],
        "Wrong workflow",
    )
    require(
        run["event"] == "pull_request"
        and run["status"] == "completed"
        and run["conclusion"] == "failure",
        "Unexpected source run state",
    )
    require(
        run["head_sha"] == policy["source_head"] and run["head_branch"] == "feat/static-preview",
        "Wrong source head",
    )
    for repo in (run["repository"], run["head_repository"]):
        require(
            repo["id"] == policy["repository_id"] and repo["full_name"] == policy["repository"],
            "Foreign repository",
        )
    prs = run["pull_requests"]
    require(len(prs) == 1 and prs[0]["number"] == 6, "Wrong source PR")
    # PR link refs are mutable; the run head and immutable merge parents bind the build.
    require(merge["sha"] == policy["source_checkout"], "Wrong source checkout")
    require(
        [p["sha"] for p in merge["parents"]] == [policy["source_base"], policy["source_head"]],
        "Wrong merge parents",
    )
    require(
        run["head_commit"]["id"] == policy["source_head"]
        and merge["tree"]["sha"] == run["head_commit"]["tree_id"],
        "Checkout differs from reviewed source",
    )
    require(jobs["total_count"] == 1 and len(jobs["jobs"]) == 1, "Unexpected source jobs")
    job = jobs["jobs"][0]
    require(
        job["id"] == policy["source_job_id"]
        and job["run_id"] == policy["run_id"]
        and job["conclusion"] == "failure",
        "Wrong source job",
    )
    steps = {s["name"]: s["conclusion"] for s in job["steps"]}
    require(
        all(steps.get(name) == result for name, result in policy["source_steps"].items()),
        "Source checks changed",
    )
    for kind, metadata in artifacts.items():
        expected = policy[kind]
        require(
            metadata["id"] == expected["id"] and metadata["name"] == expected["name"],
            "Wrong artifact",
        )
        require(
            metadata["size_in_bytes"] == expected["size"]
            and metadata["digest"] == "sha256:" + expected["sha256"],
            "Wrong archive digest/size",
        )
        require(
            metadata["expired"] is False
            and metadata["expires_at"] == expected["expires_at"]
            and now < timestamp(metadata["expires_at"]),
            "Artifact expired/changed; STOP",
        )
        source = metadata["workflow_run"]
        require(
            source["id"] == policy["run_id"] and source["head_sha"] == policy["source_head"],
            "Wrong artifact source",
        )
        require(
            source["repository_id"] == source["head_repository_id"] == policy["repository_id"],
            "Foreign artifact",
        )
        require(source["head_branch"] == "feat/static-preview", "Wrong artifact branch")


def read_archive(path, expected, files):
    """Validate every entry before extracting anything; never use extractall()."""
    require(path.is_file() and not path.is_symlink(), "Non-regular archive")
    require(path.stat().st_size == expected["size"], "Wrong archive size")
    require(digest(path.read_bytes()) == expected["sha256"], "Archive hash mismatch")
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        require(
            len(entries) == len(files) and {i.filename for i in entries} == set(files),
            "Unexpected/duplicate/traversal archive entry",
        )
        result = {}
        for entry in entries:
            path = PurePosixPath(entry.filename)
            require(
                not path.is_absolute() and ".." not in path.parts and str(path) == entry.filename,
                "Unsafe archive path",
            )
            require(
                entry.orig_filename == entry.filename and "\\" not in entry.filename,
                "Ambiguous archive path",
            )
            require(
                stat.S_ISREG(entry.external_attr >> 16) and not entry.is_dir(),
                "Link/non-regular archive entry",
            )
            require(
                not entry.flag_bits & 1 and entry.compress_type == zipfile.ZIP_DEFLATED,
                "Unsupported archive encoding",
            )
            require(entry.file_size == files[entry.filename]["bytes"], "Unexpected expanded size")
            data = archive.read(entry)
            require(digest(data) == files[entry.filename]["sha256"], "File hash mismatch")
            result[entry.filename] = data
        return result


def verify_files(policy, evidence, output):
    source = (evidence / "source-policy.json").read_bytes()
    require(digest(source) == policy["source_policy_sha256"], "Unreviewed committed manifest")
    require(json.loads(source)["artifact"]["files"] == policy["files"], "Copied manifest differs")
    require(
        digest((evidence / "source-workflow.yml").read_bytes()) == policy["workflow_sha256"],
        "Unreviewed source workflow",
    )
    files = {f["file"]: f for f in policy["files"]}
    require(
        len(files) == 4
        and set(files)
        == {
            "index.html",
            "assets/api-BN9913ub.js",
            "assets/index-BeWjSsMo.js",
            "assets/index-D5TeQHXP.css",
        },
        "Wrong preview inventory",
    )
    candidate = read_archive(evidence / "candidate.zip", policy["candidate"], files)
    audits = read_archive(evidence / "evidence.zip", policy["evidence"], policy["evidence_files"])
    require(
        json.loads(audits["artifact.json"])["files"] == policy["files"], "Source manifest disagrees"
    )
    require(
        audits["npm-full.exit"].strip() == b"1" and audits["npm-production.exit"].strip() == b"0",
        "Changed historical audits",
    )
    require(
        json.loads(audits["security-gate.json"])
        == {
            "status": "BLOCKED",
            "reason": "Exception is UNAPPROVED; operator risk decision required",
        },
        "Source security decision changed",
    )
    require(not output.exists() and not output.is_symlink(), "Output must be a new directory")
    output.mkdir()
    (output / "assets").mkdir()
    for name, data in candidate.items():
        (output / name).write_bytes(data)
    # Raw reports are evidence only and are never copied into the Pages directory.
    (evidence / "original-audits").mkdir(exist_ok=False)
    for name, data in audits.items():
        (evidence / "original-audits" / name).write_bytes(data)


def approval_comment_body(policy):
    return (
        "I approve publication of GitHub Radar v0.1.0-preview "
        f"artifact {policy['candidate']['id']}.\n"
        f"SHA-256: {policy['candidate']['sha256']}\n"
        "I accept the following historical build-toolchain risks for this exact artifact:\n"
        + "\n".join(f"- {risk}" for risk in policy["historical_risks"])
        + "\nThis approval does not authorize a rebuild, an npm exception, "
        "or a Docker/backend release."
    )


def verify_approval(policy, now):
    require(
        policy["scope"] == "github-radar/v0.1.0-preview/artifact-13256888181-only",
        "Wrong approval scope",
    )
    approval = policy["approval"]
    require(isinstance(approval, dict), "Artifact promotion is UNAPPROVED")
    require(
        re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-]{0,38}", approval.get("approver", "")),
        "Missing operator",
    )
    record = re.fullmatch(
        r"https://github\.com/zinverno/github-radar/pull/7#issuecomment-([1-9]\d*)",
        approval.get("record", ""),
    )
    require(record, "Approval must be a conversation comment on PR #7")
    require(
        timestamp("2026-11-04T00:00:00Z")
        <= timestamp(approval["approved_at"])
        <= now
        < timestamp(policy["candidate"]["expires_at"]),
        "Approval is future/expired",
    )
    require(
        approval.get("artifact_sha256") == policy["candidate"]["sha256"]
        and approval.get("accepted_historical_risks") == policy["historical_risks"],
        "Approval does not cover this artifact and historical risks",
    )
    url = f"https://api.github.com/repos/zinverno/github-radar/issues/comments/{record[1]}"
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "github-radar-preview-promotion",
            "Cache-Control": "no-cache",
        },
    )
    # Public comment reads need no token or additional workflow permissions.
    try:
        with urlopen(request, timeout=20) as response:
            require(response.status == 200 and response.geturl() == url, "Unexpected API response")
            comment = json.load(response)
    except (URLError, OSError, ValueError) as error:
        raise ValueError("Could not independently fetch the GitHub approval comment") from error
    require(isinstance(comment, dict), "Missing GitHub approval comment")
    require(
        comment.get("id") == int(record[1])
        and comment.get("url") == url
        and comment.get("html_url") == approval["record"]
        and comment.get("issue_url")
        == "https://api.github.com/repos/zinverno/github-radar/issues/7",
        "GitHub approval comment belongs to another record or PR",
    )
    user = comment.get("user")
    require(
        isinstance(user, dict)
        and user.get("type") == "User"
        and isinstance(user.get("login"), str)
        and user["login"].casefold() == approval["approver"].casefold(),
        "GitHub approval author does not match the recorded operator",
    )
    require(comment.get("created_at") == approval["approved_at"], "Approval creation time mismatch")
    require(
        comment.get("updated_at") == comment["created_at"], "Edited approval; post a new comment"
    )
    body = comment.get("body")
    require(
        isinstance(body, str)
        and body.replace("\r\n", "\n").strip() == approval_comment_body(policy),
        "GitHub comment does not explicitly accept this artifact and all four historical risks",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["verify", "approve"])
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    policy = json.loads(Path("docs/preview-promotion.json").read_text())
    now = datetime.now(UTC)
    if args.operation == "approve":
        verify_approval(policy, now)
        print(policy["candidate"]["expires_at"])
        return
    evidence = args.evidence
    def read(name):
        return json.loads((evidence / name).read_text())
    verify_provenance(
        policy,
        read("run.json"),
        read("merge.json"),
        read("jobs.json"),
        {kind: read(kind + "-metadata.json") for kind in ("candidate", "evidence")},
        now,
    )
    verify_files(policy, evidence, args.output)
    report = {
        "status": "VERIFIED_NOT_APPROVED",
        "source_run": policy["run_id"],
        "source_head": policy["source_head"],
        "artifact": policy["candidate"],
        "files": policy["files"],
        "historical_security_gate": "BLOCKED",
        "approval": policy["approval"],
    }
    (evidence / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print("Verified exact artifact and provenance; publication requires separate approval.")


if __name__ == "__main__":
    main()
