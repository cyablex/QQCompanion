"""Export an allowlisted, sanitised source tree without private Git history."""

import argparse
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

TEXT_SUFFIXES = {
    ".py",
    ".ps1",
    ".cmd",
    ".json",
    ".ts",
    ".vue",
    ".css",
    ".html",
    ".md",
    ".txt",
    ".lock",
    ".toml",
    ".yml",
    ".yaml",
}
SECRET = re.compile(
    r"\b(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}\b|\bsk-[A-Za-z0-9]{20,}\b"
)


def private_values(root):
    root = Path(root).resolve()
    values = set()
    cfg = json.loads((root / "config/user.json").read_text(encoding="utf-8-sig"))

    def collect(value):
        if isinstance(value, dict):
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, str) and re.fullmatch(r"[0-9]{5,20}", value):
            values.add(value)

    collect(
        {
            k: v
            for k, v in cfg.items()
            if k
            in {
                "owner_qq",
                "bot_qq",
                "guest_ids",
                "group_ids",
                "contact_daily_messages",
            }
        }
    )
    values.update(str(k) for k in cfg.get("contact_daily_messages", {}))
    db = root / "data/companion.sqlite3"
    if db.exists():
        with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as source:
            for (contact,) in source.execute("SELECT DISTINCT contact FROM messages"):
                values.add(str(contact).removeprefix("group:"))
            row = source.execute(
                "SELECT value FROM state WHERE key='known_friend_ids'"
            ).fetchone()
            if row:
                collect(json.loads(row[0]))
    values.discard("")
    replacements = {value: str(900000001 + i) for i, value in enumerate(sorted(values))}
    host_name = Path.home().name
    if host_name:
        replacements[host_name] = "LOCAL_USER"
    rules = root / "docs/更新日志规范.md"
    if rules.exists():
        names = re.findall(r"用户([^\s，。]{2,8})于", rules.read_text(encoding="utf-8"))
        replacements.update({name: "当前用户" for name in names})
    opening = root / "frontend/public/startup/index.html"
    if opening.exists():
        identity = re.search(
            r"identity\s*:\s*'([^']+)'", opening.read_text(encoding="utf-8")
        )
        if identity:
            replacements[identity[1]] = "本机用户"
    forbidden = set(replacements)
    forbidden.update({str(root), str(root).replace("\\", "/")})
    for name in ("onebot.token",):
        path = root / "config" / name
        if path.exists() and len(path.read_text().strip()) >= 12:
            forbidden.add(path.read_text().strip())
    # The DPAPI values are read only into memory for the final content check.
    import sys

    sys.path.insert(0, str(root / "app"))
    from settings import load_key

    for relay in (False, True):
        value = load_key(relay=relay)
        if value:
            forbidden.add(value)
    return replacements, forbidden


def sanitise(text, replacements):
    # Old test helpers sometimes added the installation's absolute app path.
    text = re.sub(
        r'^sys\.path\.insert\(0,\s*r?["\'][A-Za-z]:[/\\].*["\']\)\s*\n',
        "",
        text,
        flags=re.M,
    )
    for old, new in sorted(replacements.items(), key=lambda p: len(p[0]), reverse=True):
        text = (
            re.sub(r"(?<![0-9])" + re.escape(old) + r"(?![0-9])", lambda _: new, text)
            if old.isdigit()
            else re.sub(
                r"(?<![A-Za-z0-9_])" + re.escape(old) + r"(?![A-Za-z0-9_])",
                lambda _: new,
                text,
            )
            if old.isascii() and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", old)
            else text.replace(old, new)
        )
    return text


def sources(root):
    root = Path(root)
    for folder in (
        "app",
        "tests",
        "tools",
        "integrations",
        "plugins/astrbot_plugin_qq_companion",
        "frontend/src",
        "frontend/public",
    ):
        for path in sorted((root / folder).rglob("*")):
            if path.is_file() and not any(
                part
                in {
                    "__pycache__",
                    ".git",
                    "node_modules",
                    ".pytest_cache",
                    ".ruff_cache",
                }
                for part in path.parts
            ):
                if path.suffix in TEXT_SUFFIXES or path.name == "LICENSE":
                    yield path, path.relative_to(root).as_posix()
    for name in (
        "pyproject.toml",
        "requirements-runtime.lock",
        "requirements-desktop.lock",
        "config.example.json",
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
    ):
        yield root / name, name
    for path in sorted(root.glob("*.cmd")):
        yield path, path.name
    for name in (
        "package.json",
        "package-lock.json",
        "tsconfig.json",
        "vite.config.ts",
        "index.html",
    ):
        yield root / "frontend" / name, "frontend/" + name
    for path in sorted((root / "docs/public").glob("*.md")):
        yield (
            path,
            path.name
            if path.name in {"README.md", "CHANGELOG.md"}
            else "docs/" + path.name,
        )
    for path in sorted((root / ".github/workflows").glob("*.yml")):
        yield path, path.relative_to(root).as_posix()


def audit_tree(folder, forbidden):
    issues = []
    for path in sorted(Path(folder).rglob("*")):
        if not path.is_file() or ".git" in path.parts:
            continue
        relative = path.relative_to(folder).as_posix()
        if path.suffix not in TEXT_SUFFIXES and path.name not in {
            "LICENSE",
            ".gitignore",
            ".gitattributes",
        }:
            issues.append(dict(file=relative, rule="unreviewed-file-type"))
            continue
        text = path.read_text(encoding="utf-8-sig")
        if SECRET.search(text):
            issues.append(dict(file=relative, rule="credential-pattern"))
        for value in forbidden:
            found = (
                re.search(r"(?<![0-9])" + re.escape(value) + r"(?![0-9])", text)
                if value.isdigit()
                else re.search(
                    r"(?<![A-Za-z0-9_])" + re.escape(value) + r"(?![A-Za-z0-9_])", text
                )
                if value.isascii() and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value)
                else value in text
            )
            if found:
                issues.append(dict(file=relative, rule="private-value"))
                break
    return issues


def export_public(root, destination, replacements, forbidden):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if destination.exists():
        raise ValueError("公开导出目录必须是新目录，避免混入旧文件或提交历史。")
    destination.mkdir(parents=True)
    count = 0
    for source, name in sources(root):
        if not source.is_file():
            raise ValueError("公开发布材料尚未准备完整：" + name)
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            sanitise(source.read_text(encoding="utf-8-sig"), replacements),
            encoding="utf-8",
            newline="\n",
        )
        count += 1
    (destination / ".gitignore").write_text(
        "config/\ndata/\nassets/\nlogs/\ncache/\nbackups/\nruntime/\nastrbot/\nnapcat/\nmodels/\nwork/\noutputs/\n__pycache__/\n*.py[cod]\n.pytest_cache/\n.ruff_cache/\nnode_modules/\nfrontend/dist/\n*.dpapi\n*.token\n*.dfysave\n*.dfyresources\n.env*\n",
        encoding="utf-8",
        newline="\n",
    )
    (destination / ".gitattributes").write_text(
        "* text=auto eol=lf\n*.cmd text eol=crlf\n", encoding="utf-8", newline="\n"
    )
    public_files = sorted(
        p.relative_to(destination).as_posix()
        for p in destination.rglob("*")
        if p.is_file()
    )
    (destination / "PUBLIC_SOURCE.json").write_text(
        json.dumps(dict(format=1, files=public_files), ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    issues = audit_tree(destination, forbidden)
    if issues:
        raise ValueError(
            "公开内容审查未通过：" + json.dumps(issues, ensure_ascii=False)
        )
    return dict(files=count + 3, privacy_issues=0, history_exported=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="只生成本地脱敏源码，不创建或上传仓库。"
    )
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    replacements, forbidden = private_values(args.root)
    print(
        json.dumps(
            export_public(args.root, args.output, replacements, forbidden),
            ensure_ascii=False,
        )
    )
