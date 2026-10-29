"""Private migration is encrypted, relocatable, offline and non-destructive."""

import json
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from archive_crypto import decrypt_file, encrypt_file  # noqa: E402
from migration import export_save, read_json, restore_save, safe_members, write_json  # noqa: E402
from store import Store  # noqa: E402

PASSWORD = "test-recovery-password-123"


@pytest.fixture
def personal(tmp_path):
    root = tmp_path / "old" / "QQCompanion"
    (root / "data").mkdir(parents=True)
    write_json(
        root / "config/user.json",
        dict(
            owner_qq="12345",
            bot_qq="67890",
            persona="私人人设",
            live=True,
            paused=False,
            revision=77,
            relay_connection=dict(
                base_url="https://example.org", api_key="nested-secret"
            ),
        ),
    )
    (root / "config/api-key.dpapi").write_bytes(b"never-transport-this")
    (root / "config/onebot.token").write_text("private-token")
    store = Store(root / "data/companion.sqlite3")
    store.incoming("event-1", "12345", "私人聊天原话")
    store.remember("12345", "真正保存的记忆")
    drawing = root / "data/drawings/67890/12345" / ("a" * 32 + ".png")
    drawing.parent.mkdir(parents=True)
    drawing.write_bytes(b"private-picture")
    store.set(
        "sketches:67890:12345", [dict(id="a" * 32, file=str(drawing), title="私人作品")]
    )
    store.set("reply_job:12345", dict(event="event-1"))
    store.set(
        "digital_activities:test",
        [dict(artifact=str(root / "data/activities/note.json"))],
    )
    store.set(
        "owner_share_image:event-1",
        dict(file=str(root / "data/owner-sharing/image.jpg")),
    )
    store.set("song_pending:67890:12345:12345", dict(title="旧待选"))
    store.db.execute(
        "INSERT INTO actions VALUES('done','12345','reply','[]','succeeded',1,'receipt')"
    )
    store.db.execute(
        "INSERT INTO actions VALUES('uncertain','12345','reply','[]','sending',2,'')"
    )
    store.db.execute(
        "INSERT INTO actions VALUES('queued','12345','qzone_comment','[]','planned',3,'')"
    )
    audio = root / "data/songbook/audio" / ("b" * 32 + ".mp3")
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"private-song")
    store.db.execute(
        "INSERT INTO songs(id,title,state,file,worker_pid) VALUES(?,?,?,?,?)",
        ("b" * 32, "已完成歌曲", "ready", str(audio), 100),
    )
    store.db.execute("INSERT INTO song_requests(id,state) VALUES('old-song','waiting')")
    store.db.execute("INSERT INTO turn_tasks(id,state) VALUES('old-task','queued')")
    store.db.execute(
        "INSERT INTO qzone_authorizations(approval,state) VALUES('old-approval','queued')"
    )
    store.db.close()
    voice = root.parent / "GPTSoVITS/reference/yui"
    voice.mkdir(parents=True)
    (voice / "everyday.wav").write_bytes(b"reference")
    write_json(
        voice / "presets.json",
        dict(
            profiles=[dict(ref_audio_path=str(voice / "everyday.wav"), singing_ref_audio_path=str(voice / 'everyday.wav'))],
            results=[dict(file="old-audition")],
        ),
    )
    return root


def test_creative_records_and_preview_voice_paths_survive_restore(personal, tmp_path):
    reference = personal / 'data/songbook/references/custom.wav'
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b'private-reference')
    cfg = read_json(personal / 'config/user.json')
    cfg['singing_reference'] = str(reference)
    write_json(personal / 'config/user.json', cfg)
    folder = personal / 'data/songbook/previews' / ('b' * 32) / ('c' * 32)
    folder.mkdir(parents=True)
    (folder / 'song.mp3').write_bytes(b'preview-audio')
    write_json(folder / 'result.json', dict(state='ready', file=str(folder / 'song.mp3'), voice=dict(reference=str(reference))))
    write_json(folder / 'job.json', dict(voice_options=dict(singing_reference=str(reference))))
    info = personal / 'data/songbook/audio' / ('b' * 32 + '.json')
    write_json(info, dict(voice=dict(reference=str(personal.parent / 'GPTSoVITS/reference/yui/everyday.wav'))))
    with sqlite3.connect(personal / 'data/companion.sqlite3') as db:
        db.execute('INSERT INTO state VALUES(?,?)', ('creative:67890:12345:project', json.dumps(dict(next_step='先听前30秒'))))
    backup = tmp_path / 'creative.dfysave'
    export_save(personal, backup, PASSWORD)
    target = tmp_path / 'restored/QQCompanion'
    restore_save(backup, target, PASSWORD)
    assert Path(read_json(target / 'config/user.json')['singing_reference']).is_file()
    restored = target / folder.relative_to(personal)
    result = read_json(restored / 'result.json')
    assert result['file'] == str(restored / 'song.mp3')
    assert Path(result['voice']['reference']).is_file()
    assert Path(read_json(restored / 'job.json')['voice_options']['singing_reference']).is_file()
    expected = str(target.parent / 'GPTSoVITS/reference/yui/everyday.wav')
    assert read_json(target / info.relative_to(personal))['voice']['reference'] == expected
    assert read_json(target.parent / 'GPTSoVITS/reference/yui/presets.json')['profiles'][0]['singing_ref_audio_path'] == expected
    with sqlite3.connect(target / 'data/companion.sqlite3') as db:
        record = json.loads(db.execute("SELECT value FROM state WHERE key='creative:67890:12345:project'").fetchone()[0])
        assert record['next_step'] == '先听前30秒'


def test_save_round_trip_keeps_memory_art_and_songs_but_starts_offline(
    personal, tmp_path
):
    backup = tmp_path / "private.dfysave"
    export_save(personal, backup, PASSWORD)
    assert "私人聊天原话".encode() not in backup.read_bytes()
    target = tmp_path / "新电脑 空格" / "大肥鱼"
    assert restore_save(backup, target, PASSWORD)["offline"]
    cfg = read_json(target / "config/user.json")
    assert cfg["persona"] == "私人人设" and cfg["revision"] == 78
    assert cfg["live"] is False and cfg["paused"] is True
    assert "api_key" not in cfg["relay_connection"]
    assert (target / "config/offline-development.json").is_file()
    assert not (target / "config/api-key.dpapi").exists()
    assert not (target / "config/onebot.token").exists()
    with sqlite3.connect(target / "data/companion.sqlite3") as db:
        assert db.execute("SELECT text FROM messages").fetchone()[0] == "私人聊天原话"
        assert db.execute("SELECT text FROM memory").fetchone()[0] == "真正保存的记忆"
        activity = json.loads(
            db.execute(
                "SELECT value FROM state WHERE key='digital_activities:test'"
            ).fetchone()[0]
        )
        assert activity[0]["artifact"] == str(target / "data/activities/note.json")
        image = json.loads(
            db.execute(
                "SELECT value FROM state WHERE key='owner_share_image:event-1'"
            ).fetchone()[0]
        )
        assert image["file"] == str(target / "data/owner-sharing/image.jpg")
        drawings = json.loads(
            db.execute(
                "SELECT value FROM state WHERE key LIKE 'sketches:%'"
            ).fetchone()[0]
        )
        assert Path(drawings[0]["file"]).is_file()
        song = db.execute("SELECT state,file,worker_pid FROM songs").fetchone()
        assert song[0] == "ready" and Path(song[1]).is_file() and song[2] == 0
        assert (
            db.execute("SELECT state FROM actions WHERE id='done'").fetchone()[0]
            == "succeeded"
        )
        assert (
            db.execute("SELECT state FROM actions WHERE id='uncertain'").fetchone()[0]
            == "unknown"
        )
        assert (
            db.execute("SELECT state FROM actions WHERE id='queued'").fetchone()[0]
            == "cancelled"
        )
        assert db.execute("SELECT state FROM turn_tasks").fetchone()[0] == "blocked"
        assert (
            db.execute("SELECT state FROM song_requests").fetchone()[0] == "cancelled"
        )
        assert (
            db.execute("SELECT state FROM qzone_authorizations").fetchone()[0]
            == "blocked"
        )
        assert not db.execute(
            "SELECT 1 FROM state WHERE key LIKE 'reply_job:%' OR key LIKE 'song_pending:%'"
        ).fetchone()
        assert db.execute(
            "SELECT value FROM state WHERE key='offline_ignore_before:12345'"
        ).fetchone()
    presets = read_json(target.parent / "GPTSoVITS/reference/yui/presets.json")
    assert Path(presets["profiles"][0]["ref_audio_path"]).is_file()
    assert "results" not in presets
    with sqlite3.connect(
        (personal / "data/companion.sqlite3").as_uri() + "?mode=ro", uri=True
    ) as db:
        assert db.execute("SELECT state FROM turn_tasks").fetchone()[0] == "queued"
    assert read_json(personal / "config/user.json")["live"] is True


def test_wrong_password_never_installs_anything(personal, tmp_path):
    backup = tmp_path / "private.dfysave"
    export_save(personal, backup, PASSWORD)
    target = tmp_path / "new" / "QQCompanion"
    with pytest.raises(ValueError, match="口令错误"):
        restore_save(backup, target, "another-test-password")
    assert not target.exists()


def test_restore_refuses_existing_personal_installation(personal, tmp_path):
    backup = tmp_path / "private.dfysave"
    export_save(personal, backup, PASSWORD)
    before = (personal / "config/user.json").read_bytes()
    with pytest.raises(ValueError, match="全新目录"):
        restore_save(backup, personal, PASSWORD)
    assert (personal / "config/user.json").read_bytes() == before


def test_export_never_overwrites_an_existing_backup(personal, tmp_path):
    backup = tmp_path / "private.dfysave"
    backup.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        export_save(personal, backup, PASSWORD)
    assert backup.read_bytes() == b"existing"


def test_tampered_ciphertext_is_rejected_and_plaintext_is_removed(tmp_path):
    plain = tmp_path / "plain.zip"
    encrypted = tmp_path / "encrypted.dfysave"
    output = tmp_path / "decoded.zip"
    plain.write_bytes(b"private bytes" * 100)
    encrypt_file(plain, encrypted, PASSWORD)
    data = bytearray(encrypted.read_bytes())
    data[-20] ^= 1
    encrypted.write_bytes(data)
    with pytest.raises(ValueError, match="损坏"):
        decrypt_file(encrypted, output, PASSWORD)
    assert not output.exists()


@pytest.mark.parametrize(
    "name",
    [
        "../outside",
        "main/../../outside",
        "main/C:/outside",
        "main/evil\\file",
        "/main/outside",
        "other/file",
    ],
)
def test_unsafe_archive_names_are_rejected_before_extraction(tmp_path, name):
    path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(path, "w") as archive:
        entry = zipfile.ZipInfo("placeholder")
        entry.filename = name
        archive.writestr(entry, "anything")
    with zipfile.ZipFile(path) as archive, pytest.raises(ValueError):
        list(safe_members(archive, {"main"}))


def test_archive_credentials_are_excluded_even_inside_encrypted_zip(personal, tmp_path):
    backup = tmp_path / "private.dfysave"
    export_save(personal, backup, PASSWORD)
    decrypted = tmp_path / "decoded.zip"
    decrypt_file(backup, decrypted, PASSWORD)
    with zipfile.ZipFile(decrypted) as archive:
        assert not any(
            name.endswith((".token", ".dpapi")) for name in archive.namelist()
        )
        assert b"nested-secret" not in archive.read("main/config/user.json")
