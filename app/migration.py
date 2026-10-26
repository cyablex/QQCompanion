"""Export personal state and restore it offline into a clean installation.

No credentials, login sessions, PID files or supervisor state are transported.
Production reads use SQLite read-only URIs, never Store construction.
"""

import argparse
import getpass
import json
import re
import shutil
import sqlite3
import stat
import tempfile
import time
import zipfile
from contextlib import closing
from pathlib import Path, PurePosixPath, PureWindowsPath

from archive_crypto import decrypt_file, encrypt_file

SAVE_FORMAT = 1
DATA_FOLDERS = (
    "activities",
    "attachments",
    "owner-sharing",
    "drawings",
    "knowledge_base",
    "songbook/audio",
    "songbook/references",
    "t2i_templates",
    "webchat",
)


def clean_config(value):
    if isinstance(value, dict):
        return {
            key: clean_config(item)
            for key, item in value.items()
            if not re.fullmatch(
                r"(?:.*api.?key|.*token|password|secret|authorization|cookie|credential|headers)",
                key,
                re.I,
            )
        }
    if isinstance(value, list):
        return [clean_config(item) for item in value]
    return value


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def product_version(root):
    path = Path(root) / "app/desktop_api.py"
    match = (
        re.search(r'^VERSION = "([^"]+)"', path.read_text(encoding="utf-8"), re.M)
        if path.is_file()
        else None
    )
    return match[1] if match else "unknown"


def _files(folder):
    if folder.is_symlink():
        raise ValueError("备份范围不能包含符号链接。")
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            raise ValueError("备份范围不能包含符号链接。")
        if path.is_file():
            yield path


def safe_members(archive, allowed_roots):
    """Validate before extraction, including Windows drive names and links."""
    seen = set()
    for entry in archive.infolist():
        name = entry.filename
        path = PurePosixPath(name)
        if (
            "\\" in name
            or "\\" in entry.orig_filename
            or path.is_absolute()
            or ".." in path.parts
            or not path.parts
            or path.parts[0] not in allowed_roots
            or any(":" in part or part.endswith((".", " ")) for part in path.parts)
            or stat.S_ISLNK(entry.external_attr >> 16)
        ):
            raise ValueError("备份内包含不允许的路径。")
        key = name.casefold().rstrip("/")
        if key in seen:
            raise ValueError("备份内存在重复路径。")
        seen.add(key)
        yield entry


def export_save(root, output, password):
    root, output = Path(root).resolve(), Path(output).resolve()
    db_path = root / "data/companion.sqlite3"
    config_path = root / "config/user.json"
    if not db_path.is_file() or not config_path.is_file():
        raise ValueError("没有找到可备份的个人配置与聊天存档。")
    if output.exists():
        raise FileExistsError("目标备份已经存在，请使用新文件名。")
    output.parent.mkdir(parents=True, exist_ok=True)
    config_bytes = config_path.read_bytes()
    items = [(config_path, "main/config/user.json")]
    for name in DATA_FOLDERS:
        folder = root / "data" / name
        if folder.exists():
            items.extend(
                (p, "main/" + p.relative_to(root).as_posix()) for p in _files(folder)
            )
    for job in (root / 'data/songbook/previews').glob('*/*/job.json'):
        for name in ('job.json', 'result.json', 'song.mp3', 'converted/pitch.json'):
            path = job.parent / name
            if path.is_file():
                items.append((path, 'main/' + path.relative_to(root).as_posix()))
    if (root / "assets").exists():
        items.extend(
            (p, "main/" + p.relative_to(root).as_posix())
            for p in _files(root / "assets")
        )
    voice = root.parent / "GPTSoVITS"
    if (voice / "reference").exists():
        items.extend(
            (p, "voice/" + p.relative_to(voice).as_posix())
            for p in _files(voice / "reference")
        )
    if (voice / "tts_local.yaml").is_file():
        items.append((voice / "tts_local.yaml", "voice/tts_local.yaml"))
    stamps = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p, _ in items}
    with tempfile.TemporaryDirectory(prefix="dfy-save-", dir=output.parent) as scratch:
        scratch = Path(scratch)
        with closing(
            sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)
        ) as source:
            data_version = source.execute("PRAGMA data_version").fetchone()[0]
            with (
                closing(sqlite3.connect(scratch / "snapshot.sqlite3")) as target,
                target,
            ):
                source.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("数据库备份未通过完整性检查。")
            with zipfile.ZipFile(
                scratch / "save.zip", "x", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                archive.writestr(
                    "manifest.json",
                    json.dumps(
                        dict(
                            format=SAVE_FORMAT,
                            kind="personal-save",
                            app_version=product_version(root),
                            created=time.time(),
                            source_root=str(root),
                            source_voice_root=str(voice),
                            credentials_included=False,
                            login_sessions_included=False,
                        ),
                        ensure_ascii=False,
                    ),
                )
                archive.write(
                    scratch / "snapshot.sqlite3", "main/data/companion.sqlite3"
                )
                for path, name in items:
                    if name.startswith('main/data/songbook/previews/') and name.endswith('/job.json'):
                        job = read_json(path)
                        job.pop('pid', None)
                        job.pop('worker_pid', None)
                        archive.writestr(name, json.dumps(job, ensure_ascii=False))
                        continue
                    if name == "main/config/user.json":
                        archive.writestr(
                            name,
                            json.dumps(
                                clean_config(json.loads(config_bytes)),
                                ensure_ascii=False,
                            ),
                        )
                    else:
                        archive.write(path, name)
            if source.execute("PRAGMA data_version").fetchone()[0] != data_version:
                raise RuntimeError("备份期间有存档写入，请短暂停止聊天后台后重新导出。")
        if config_path.read_bytes() != config_bytes or any(
            not p.is_file() or (p.stat().st_size, p.stat().st_mtime_ns) != stamp
            for p, stamp in stamps.items()
        ):
            raise RuntimeError("备份期间配置或作品发生变化，请停止修改后重新导出。")
        encrypt_file(scratch / "save.zip", output, password)
    return dict(files=len(items) + 1, bytes=output.stat().st_size)


def relocate_path(value, old_root, new_root):
    """Map complete registered path fields, preserving unrelated chat text."""
    old, candidate = PureWindowsPath(old_root), PureWindowsPath(value)
    try:
        relative = candidate.relative_to(old)
    except ValueError:
        return value
    return str(Path(new_root).joinpath(*relative.parts))


def _restored_database(path, old_root, new_root, restored_at):
    with closing(sqlite3.connect(path)) as db, db:
        db.row_factory = sqlite3.Row
        tables = {
            r[0]
            for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not {"state", "messages", "events", "memory", "actions"} <= tables:
            raise ValueError("存档缺少必要的数据表。")
        contacts = {
            str(r[0]) for r in db.execute("SELECT DISTINCT contact FROM messages")
        }
        for row in db.execute("SELECT key,value FROM state").fetchall():
            key = row["key"]
            if key.startswith(("sketches:", "digital_activities:")):
                records = json.loads(row["value"])
                for record in records:
                    if isinstance(record, dict) and isinstance(record.get("file"), str):
                        record["file"] = relocate_path(
                            record["file"], old_root, new_root
                        )
                    if isinstance(record, dict) and isinstance(
                        record.get("artifact"), str
                    ):
                        record["artifact"] = relocate_path(
                            record["artifact"], old_root, new_root
                        )
                db.execute(
                    "UPDATE state SET value=? WHERE key=?",
                    (json.dumps(records, ensure_ascii=False), key),
                )
            elif key.startswith("owner_share_image:"):
                record = json.loads(row["value"])
                if isinstance(record.get("file"), str):
                    record["file"] = relocate_path(record["file"], old_root, new_root)
                    db.execute(
                        "UPDATE state SET value=? WHERE key=?",
                        (json.dumps(record, ensure_ascii=False), key),
                    )
            elif key.startswith(
                ("reply_job:", "song_pending:", "offline_context:")
            ) or key in {
                "own_post_requested",
                "qzone_probe_requested",
                "qzone_comment_test_requested",
            }:
                db.execute("DELETE FROM state WHERE key=?", (key,))
        for contact in contacts:
            db.execute(
                "INSERT OR REPLACE INTO state VALUES(?,?)",
                ("offline_ignore_before:" + contact, json.dumps(restored_at)),
            )
        db.execute("UPDATE actions SET state='unknown' WHERE state='sending'")
        db.execute(
            "UPDATE actions SET state='cancelled',result='从存档恢复，旧计划不自动执行。' WHERE state='planned'"
        )
        updates = {
            "turn_tasks": [
                ("UPDATE turn_tasks SET state='unknown' WHERE state='running'", ()),
                (
                    "UPDATE turn_tasks SET state='blocked',updated=? WHERE state IN ('queued','waiting','prepared','awaiting_input','awaiting_source')",
                    (restored_at,),
                ),
            ],
            "qzone_authorizations": [
                (
                    "UPDATE qzone_authorizations SET state='unknown' WHERE state='sending'",
                    (),
                ),
                (
                    "UPDATE qzone_authorizations SET state='blocked',result='从存档恢复，需要重新确认。' WHERE state='queued'",
                    (),
                ),
            ],
            "qzone_approvals": [
                (
                    "UPDATE qzone_approvals SET state='unknown' WHERE state='sending'",
                    (),
                ),
                (
                    "UPDATE qzone_approvals SET state='expired' WHERE state IN ('pending','approved')",
                    (),
                ),
            ],
            "song_requests": [
                ("UPDATE song_requests SET state='unknown' WHERE state='sending'", ()),
                (
                    "UPDATE song_requests SET state='cancelled',result='从存档恢复，旧点歌不自动发送。' WHERE state IN ('waiting','ready','awaiting_source')",
                    (),
                ),
            ],
            "songs": [
                ("UPDATE songs SET worker_pid=0", ()),
                (
                    "UPDATE songs SET state='failed',progress='从存档恢复，需要重新点歌。' WHERE state NOT IN ('ready','deleted')",
                    (),
                ),
            ],
            "cm_followups": [
                ("UPDATE cm_followups SET state='cancelled' WHERE state='pending'", ())
            ],
            "cm_meta": [("UPDATE cm_meta SET generation=generation+1", ())],
            "friends": [
                ("UPDATE friends SET state='unknown' WHERE state='processing'", ()),
                ("UPDATE friends SET state='cancelled' WHERE state='pending'", ()),
            ],
            "calls": [
                ("UPDATE calls SET state='interrupted' WHERE state='reserved'", ())
            ],
        }
        for table, operations in updates.items():
            if table in tables:
                for sql, parameters in operations:
                    db.execute(sql, parameters)
        if "songs" in tables:
            for row in db.execute(
                "SELECT id,file FROM songs WHERE file!=''"
            ).fetchall():
                db.execute(
                    "UPDATE songs SET file=? WHERE id=?",
                    (relocate_path(row["file"], old_root, new_root), row["id"]),
                )
        db.execute(
            "INSERT OR REPLACE INTO state VALUES(?,?)",
            ("migration_restored_at", json.dumps(restored_at)),
        )
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("恢复后的数据库未通过完整性检查。")


def restore_save(backup, target, password):
    target = Path(target).resolve()
    # Import is for a new location. A running or existing personal installation
    # must never be overwritten by a save restore.
    for name in ("config", "data", "assets"):
        folder = target / name
        if folder.exists() and any(folder.iterdir()):
            raise ValueError("目标已有配置或存档，请选择全新目录。")
    voice_target = target.parent / "GPTSoVITS"
    installed = []
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".dfy-restore-", dir=target.parent
    ) as scratch:
        scratch = Path(scratch)
        decrypt_file(backup, scratch / "save.zip", password)
        with zipfile.ZipFile(scratch / "save.zip") as archive:
            entries = list(safe_members(archive, {"manifest.json", "main", "voice"}))
            manifest = json.loads(archive.read("manifest.json"))
            if (
                manifest.get("format") != SAVE_FORMAT
                or manifest.get("kind") != "personal-save"
            ):
                raise ValueError("不支持此存档版本或文件类型。")
            # An authenticated file still needs its member scope validated before
            # extraction; only these persistent data directories are importable.
            for entry in entries:
                name = entry.filename
                if name.startswith("main/") and not name.startswith(
                    ("main/config/", "main/data/", "main/assets/")
                ):
                    raise ValueError("私人存档包含非存档文件。")
                if name.startswith("main/config/") and name != "main/config/user.json":
                    raise ValueError("私人存档不能恢复凭据或本机令牌。")
                if (
                    name.startswith("voice/")
                    and not name.startswith("voice/reference/")
                    and name != "voice/tts_local.yaml"
                ):
                    raise ValueError("私人存档包含非参考音色文件。")
            archive.extractall(scratch / "unpacked", members=entries)
        unpacked = scratch / "unpacked"
        for name in ("reference", "tts_local.yaml"):
            if (unpacked / "voice" / name).exists() and (voice_target / name).exists():
                raise ValueError("目标已有参考音色配置，请选择另一套空目录。")
        cfg = clean_config(read_json(unpacked / "main/config/user.json"))
        if cfg.get('singing_reference'):
            cfg['singing_reference'] = relocate_path(cfg['singing_reference'], manifest['source_root'], target)
        cfg.update(live=False, paused=True, revision=int(cfg.get("revision", 0)) + 1)
        write_json(unpacked / "main/config/user.json", cfg)
        write_json(
            unpacked / "main/config/offline-development.json",
            dict(reason="restored-save", restored_at=time.time()),
        )
        _restored_database(
            unpacked / "main/data/companion.sqlite3",
            manifest["source_root"],
            target,
            time.time(),
        )
        catalog = unpacked / "main/assets/memes/catalog.json"
        if catalog.is_file():
            values = read_json(catalog)
            for item in values:
                if isinstance(item.get("file"), str):
                    item["file"] = relocate_path(
                        item["file"], manifest["source_root"], target
                    )
            write_json(catalog, values)
        for artifact in (unpacked / "main/data/activities").rglob("*.json"):
            record = read_json(artifact)
            if isinstance(record, dict) and isinstance(record.get("artifact"), str):
                record["artifact"] = relocate_path(
                    record["artifact"], manifest["source_root"], target
                )
                write_json(artifact, record)
        presets = unpacked / "voice/reference/yui/presets.json"
        for artifact in (unpacked / 'main/data/songbook/previews').glob('*/*/*.json'):
            record = read_json(artifact)
            if not isinstance(record, dict):
                continue
            if isinstance(record.get('file'), str):
                record['file'] = relocate_path(record['file'], manifest['source_root'], target)
            for field in ('voice', 'voice_options'):
                if isinstance(record.get(field), dict):
                    for key in ('reference', 'singing_reference'):
                        if isinstance(record[field].get(key), str):
                            record[field][key] = relocate_path(record[field][key], manifest['source_root'], target)
            write_json(artifact, record)
        if presets.is_file():
            values = read_json(presets)
            for profile in values.get("profiles", []):
                if isinstance(profile.get("ref_audio_path"), str):
                    profile["ref_audio_path"] = relocate_path(
                        profile["ref_audio_path"],
                        manifest["source_voice_root"],
                        voice_target,
                    )
            # Audition output paths describe the old machine, not references.
            values.pop("results", None)
            write_json(presets, values)
        try:
            for source_root, destination in [
                (unpacked / "main", target),
                (unpacked / "voice", voice_target),
            ]:
                if not source_root.exists():
                    continue
                destination.mkdir(parents=True, exist_ok=True)
                for source in source_root.iterdir():
                    final = destination / source.name
                    if final.exists():
                        final.rmdir()  # Only the empty directories checked above.
                    shutil.move(str(source), str(final))
                    installed.append(final)
        except BaseException:
            for path in reversed(installed):
                path = path.resolve()
                if not (
                    path.is_relative_to(target) or path.is_relative_to(voice_target)
                ):
                    raise RuntimeError("恢复回滚路径超出目标目录。")
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
            raise
    return dict(restored=True, offline=True, credentials_required=True)


def enable_restored(root):
    root = Path(root).resolve()
    marker = root / "config/offline-development.json"
    if not marker.is_file() or read_json(marker).get("reason") not in {
        "restored-save",
        "new-install",
    }:
        raise ValueError("这不是等待启用的新安装或恢复目录。")
    cfg = read_json(root / "config/user.json")
    if any(
        not re.fullmatch(r"[1-9][0-9]{4,19}", str(cfg.get(key, "")))
        for key in ("owner_qq", "bot_qq")
    ):
        raise ValueError("请先在设置中填写主人和机器人账号。")
    relay = cfg.get("model_route") == "relay"
    if not (
        cfg.get("relay_connection", {}).get("model") if relay else cfg.get("model")
    ):
        raise ValueError("请先配置实际可用的聊天模型。")
    credential = root / "config" / ("relay-api-key.dpapi" if relay else "api-key.dpapi")
    if not credential.is_file():
        raise ValueError("请先在新电脑重新保存 API Key。")
    from settings import crypt_key

    try:
        if not crypt_key(credential.read_bytes(), decrypt=True).decode("utf-8").strip():
            raise ValueError("empty")
    except Exception:
        raise ValueError("本机无法读取 API Key，请重新填写保存。") from None
    cfg.update(live=True, paused=False, revision=int(cfg.get("revision", 0)) + 1)
    write_json(root / "config/user.json", cfg)
    marker.unlink()
    return dict(enabled=True, started=False)


def main():
    parser = argparse.ArgumentParser(
        description="加密导出/离线恢复大肥鱼私人存档，不上传任何内容。"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export")
    export.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    export.add_argument("--output", type=Path)
    restore = sub.add_parser("restore")
    restore.add_argument("--backup", type=Path)
    restore.add_argument("--target", type=Path)
    for name in ("resources-export", "resources-restore"):
        resources = sub.add_parser(name)
        resources.add_argument(
            "--root", type=Path, default=Path(__file__).resolve().parents[1]
        )
        resources.add_argument("--archive", type=Path)
    activate = sub.add_parser("activate")
    activate.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args()
    if args.command == "activate":
        if (
            input("请先停止旧电脑的大肥鱼。输入「启用」确认启用本目录：").strip()
            != "启用"
        ):
            raise ValueError("未启用。")
        print(json.dumps(enable_restored(args.root), ensure_ascii=False))
        return
    password = getpass.getpass("恢复口令（至少12个字符，不会显示）：")
    if args.command == "export":
        if password != getpass.getpass("再次输入恢复口令："):
            raise ValueError("两次恢复口令不一致。")
        output = args.output or Path(
            input("备份文件完整路径（以 .dfysave 结尾）：").strip().strip('"')
        )
        report = export_save(args.root, output, password)
    elif args.command == "restore":
        backup = args.backup or Path(input("加密存档文件完整路径：").strip().strip('"'))
        target = args.target or Path(
            input("新大肥鱼目录（不能有旧存档）：").strip().strip('"')
        )
        report = restore_save(backup, target, password)
    else:
        from resource_archive import export_resources, restore_resources

        archive = args.archive or Path(
            input("加密离线资源包完整路径：").strip().strip('"')
        )
        if args.command == "resources-export":
            if password != getpass.getpass("再次输入恢复口令："):
                raise ValueError("两次恢复口令不一致。")
            report = export_resources(
                args.root,
                archive,
                password,
                progress=lambda data: print(data, flush=True),
            )
        else:
            report = restore_resources(
                archive,
                args.root,
                password,
                progress=lambda data: print(data, flush=True),
            )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, FileExistsError) as exc:
        raise SystemExit(str(exc)) from None
