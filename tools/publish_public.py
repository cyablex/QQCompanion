"""Publish a sanitised snapshot on top of the public repository's history."""

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

from public_release import audit_tree, export_public, private_values

REPOSITORY = "https://github.com/cyablex/QQCompanion.git"


def git(folder, *args):
    result = subprocess.run(
        ["git", "-C", str(folder), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"},
    )
    return result.stdout.strip()


def sync_snapshot(snapshot, checkout):
    """Remove stale exported files, preserving independently added public files."""
    snapshot, checkout = Path(snapshot), Path(checkout).resolve()
    manifest = checkout / "PUBLIC_SOURCE.json"
    previous = json.loads(manifest.read_text(encoding="utf-8"))["files"]
    for name in previous:
        target = (checkout / name).resolve()
        if not target.is_relative_to(checkout) or ".git" in target.relative_to(checkout).parts:
            raise ValueError("Invalid public manifest path")
        if not (snapshot / name).is_file() and target.is_file():
            target.unlink()
    shutil.copytree(snapshot, checkout, dirs_exist_ok=True)


def publish(root, output, message, push=False):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("发布输出目录必须是新目录。")
    replacements, forbidden = private_values(root)
    report = export_public(root, output / "source", replacements, forbidden)
    checkout = output / "repository"
    # Only public history is cloned; the installed repository is never a parent.
    git(output, "clone", "--branch", "main", "--single-branch", REPOSITORY, str(checkout))
    git(checkout, "config", "user.name", "cyablex")
    git(checkout, "config", "user.email", "233666635+cyablex@users.noreply.github.com")
    sync_snapshot(output / "source", checkout)
    issues = audit_tree(checkout, forbidden)
    if issues:
        raise ValueError("公开内容审查未通过：" + json.dumps(issues, ensure_ascii=False))
    git(checkout, "add", "--all")
    if git(checkout, "diff", "--cached", "--name-only"):
        git(checkout, "commit", "-m", message)
    sha = git(checkout, "rev-parse", "HEAD")
    if push:
        git(checkout, "push", "origin", "HEAD:main")
        remote = git(checkout, "ls-remote", "origin", "refs/heads/main").split()[0]
        if remote != sha:
            raise RuntimeError("远端 main 与本次提交不同，请检查后再确认同步状态。")
    report.update(
        commit=sha,
        pushed=push,
        url=f"https://github.com/cyablex/QQCompanion/commit/{sha}",
        checkout=str(checkout),
    )
    (output / "publication.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--message", required=True)
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(publish(args.root, args.output, args.message, args.push), ensure_ascii=False))
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.stderr.strip() or "Git 发布失败，尚未确认同步成功。") from None
