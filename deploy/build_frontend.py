"""Build only reviewed frontend inputs, without runtime secrets, dotenv or public/ files."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="radar-build-") as directory:
        staging = Path(directory)
        for name in (
            "package.json",
            "package-lock.json",
            "index.html",
            "vite.config.ts",
            "tsconfig.json",
            "src",
        ):
            source = ROOT / "web" / name
            if source.is_dir():
                shutil.copytree(source, staging / name)
            else:
                shutil.copyfile(source, staging / name)
        env = {
            "PATH": os.environ["PATH"],
            "npm_config_cache": str(staging / ".npm"),
            "npm_config_userconfig": "/dev/null",
            "npm_config_globalconfig": str(staging / "empty-global-npmrc"),
        }
        for command in (
            ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
            ["npm", "run", "build"],
        ):
            subprocess.run(command, cwd=staging, env=env, check=True)
        dist = staging / "dist"
        if not (dist / "index.html").is_file():
            raise RuntimeError("Frontend entrypoint missing")
        for path in dist.rglob("*"):
            if path.is_file() and (
                path.suffix not in {".html", ".js", ".css"}
                or path.is_symlink()
                or "sourceMappingURL=" in path.read_text()
            ):
                raise RuntimeError("Unexpected public artifact; review build inputs")
        destination = ROOT / "web" / "dist"
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(dist, destination)
        for path in sorted(destination.rglob("*")):
            if path.is_file():
                print(hashlib.sha256(path.read_bytes()).hexdigest(), path.relative_to(ROOT))


if __name__ == "__main__":
    main()
