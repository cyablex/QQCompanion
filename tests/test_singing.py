# ruff: noqa: E402
import importlib.util
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import desktop_api
import group_chat
import song_source
from engine import Engine
from group_chat import GroupChats
from settings import atomic_json
from singing import Singing
from store import Store, CHAT_KINDS


@pytest.fixture
def songs(tmp_path, monkeypatch):
    root = tmp_path / "QQCompanion"
    (root / "data").mkdir(parents=True)
    (root / "logs").mkdir()
    store = Store(root / "data/companion.sqlite3")
    cfg = dict(owner_qq="12345", bot_qq="67890", live=True, paused=False, guests=True, guest_mode="open", revision=1,
               name="fish", persona="kind", owner_title="主人", singing_enabled=True, game_mode=False,
               daily_calls=1000, daily_cny=3, monthly_cny=60, daily_chat_messages=500, guest_daily_messages=50,
               group_enabled=True, group_ids=["99999"], group_interjections=False)
    sent, model_calls, native, started = [], [], [], []
    parsed = dict(intent="request", title="雨爱", artist="")

    async def generate(*args, **kwargs):
        return "正常聊天回复", None

    async def aux(kind, system, contexts, **kwargs):
        model_calls.append((system, contexts))
        return json.dumps(parsed, ensure_ascii=False)

    async def send(contact, text, **options):
        sent.append((contact, text, options))
        return dict(message_id=len(sent))

    async def check():
        pass

    async def action(name, **kwargs):
        native.append((name, kwargs))
        return dict(message_id=len(native))

    engine = Engine(store, generate, send, lambda: cfg)
    engine.generate_aux = aux
    plugin = SimpleNamespace(store=store, engine=engine, generate=generate, check_account=check,
                             bot=SimpleNamespace(call_action=action))
    groups = GroupChats(plugin)
    groups.engine.config_reader = lambda: cfg
    groups.engine.generate_aux = aux
    monkeypatch.setattr(group_chat, "read_settings", lambda: cfg)
    plugin.groups = groups
    manager = Singing(plugin, root=root, config_reader=lambda: cfg)
    plugin.singing = manager
    engine.singing = groups.engine.singing = manager
    alive = set()

    def start(song):
        started.append(song["id"])
        alive.add(123456)
        store.db.execute("UPDATE songs SET state='processing',worker_pid=123456 WHERE id=?", (song["id"],))

    monkeypatch.setattr(manager, "start_worker", start)
    monkeypatch.setattr(manager, "worker_alive", lambda pid: pid in alive)
    monkeypatch.setattr(manager, "stop_worker", lambda pid: alive.discard(pid))
    env = SimpleNamespace(root=root, store=store, cfg=cfg, engine=engine, groups=groups, plugin=plugin, manager=manager,
                          sent=sent, parsed=parsed, model_calls=model_calls, native=native, started=started, alive=alive)
    yield env
    store.db.close()


def ready(env, title="雨爱", artist="司凤", aliases=("司冯",)):
    song = env.manager.library.add(title, artist, aliases=aliases)
    path = env.manager.library.audio_path(song["id"]).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"local-audio")
    env.store.db.execute("UPDATE songs SET state='ready',file=?,bytes=?,duration=102,progress='已收录' WHERE id=?",
                         (str(path), path.stat().st_size, song["id"]))
    return env.manager.library.get(song["id"])


def incoming(env, text, contact="12345", event="event"):
    env.store.incoming(event, contact, text)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["唱一首司冯版雨爱", "给我唱司凤的《雨爱》", "能不能唱《雨爱》（司凤版）"])
async def test_cached_song_sends_real_voice_without_model_or_generation(songs, text):
    song = ready(songs)
    incoming(songs, text)
    assert await songs.engine.reply("12345")
    assert songs.sent[-1][2] == dict(song_file=song["file"], song_id=song["id"])
    assert not songs.model_calls and not songs.started
    assert songs.store.db.execute("SELECT state FROM song_requests").fetchone()[0] == "sent"
    assert songs.store.get("last_delivery:12345")["mode"] == "voice"
    assert songs.store.sent_count(0, kinds=CHAT_KINDS) == 1


@pytest.mark.asyncio
async def test_missing_artist_is_asked_even_when_model_guesses_original_singer(songs):
    songs.parsed["artist"] = "杨丞琳"
    incoming(songs, "唱雨爱")
    assert await songs.engine.reply("12345")
    assert "哪位歌手" in songs.sent[-1][1]
    assert not songs.store.db.execute("SELECT 1 FROM songs").fetchone()
    assert not songs.store.db.execute("SELECT 1 FROM song_requests").fetchone()


@pytest.mark.asyncio
async def test_artist_reply_completes_pending_title_and_is_scoped_to_requester(songs):
    ready(songs)
    incoming(songs, "唱雨爱")
    await songs.engine.reply("12345")
    incoming(songs, "司凤版", event="answer")
    await songs.engine.reply("12345")
    assert songs.sent[-1][2].get("song_file")
    assert len(songs.model_calls) == 1


@pytest.mark.asyncio
async def test_new_song_acknowledges_then_shared_job_finishes_for_original_contacts(songs):
    songs.parsed.update(title="稻香", artist="周杰伦")
    for contact in ("12345", "11111"):
        incoming(songs, "唱一首周杰伦的稻香", contact, "new:" + contact)
        await songs.engine.reply(contact)
    assert len(songs.manager.library.list()) == 1
    assert all("做好后就发到这里" in s[1] for s in songs.sent)
    await songs.manager.tick()
    assert len(songs.started) == 1
    song = songs.manager.library.list()[0]
    songs.alive.clear()
    work = songs.manager.library.directory / "work" / song["id"]
    work.mkdir(parents=True)
    file = songs.manager.library.audio_path(song["id"]).resolve()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(b"complete-cover")
    atomic_json(work / "result.json", dict(state="ready", file=str(file), duration=240, bytes=file.stat().st_size,
                                           source_url="https://www.bilibili.com/video/BV1i2h96bEwj/", source_title="稻香"))
    incoming(songs, "先聊点别的", event="later-chat")
    await songs.manager.tick()
    deliveries = [s for s in songs.sent if s[2].get("song_file")]
    assert {s[0] for s in deliveries} == {"12345", "11111"}
    assert len(deliveries) == 2 and not work.exists()
    assert len(songs.started) == 1
    assert songs.store.sent_count(0, kinds=CHAT_KINDS) == 4


@pytest.mark.asyncio
async def test_discussing_cached_song_does_not_trigger_singing(songs):
    ready(songs)
    songs.parsed["intent"] = "skip"
    incoming(songs, "司凤唱的雨爱为什么这么魔性")
    await songs.engine.reply("12345")
    assert not songs.store.db.execute("SELECT 1 FROM song_requests").fetchone()
    assert not songs.sent[-1][2].get("song_file")


@pytest.mark.asyncio
async def test_split_song_name_and_artist_messages_are_combined(songs):
    ready(songs)
    incoming(songs, "唱一首雨爱", event="first")
    incoming(songs, "司凤版", event="second")
    await songs.engine.reply("12345")
    assert songs.sent[-1][2].get("song_file") and not songs.model_calls


@pytest.mark.asyncio
async def test_unknown_delivery_is_not_retried_or_replaced_with_fake_success(songs):
    ready(songs)
    calls = []

    async def uncertain(*args, **kwargs):
        calls.append(kwargs)
        raise TimeoutError("receipt lost")

    songs.engine.send = uncertain
    incoming(songs, "唱司凤版雨爱")
    await songs.engine.reply("12345")
    await songs.manager.tick()
    assert len(calls) == 1
    assert songs.store.db.execute("SELECT state FROM song_requests").fetchone()[0] == "unknown"
    assert songs.store.get("last_delivery:12345")["mode"] == "unknown"


@pytest.mark.asyncio
async def test_game_mode_defers_cached_song_and_new_conversion_then_resumes(songs):
    ready(songs)
    songs.cfg["game_mode"] = True
    incoming(songs, "唱司凤版雨爱")
    await songs.engine.reply("12345")
    assert "游戏模式" in songs.sent[-1][1] and not songs.sent[-1][2].get("song_file")
    await songs.manager.tick()
    assert not any(send[2].get("song_file") for send in songs.sent)
    songs.cfg["game_mode"] = False
    await songs.manager.tick()
    assert songs.sent[-1][2].get("song_file")
    songs.cfg["game_mode"] = True
    songs.parsed.update(title="稻香", artist="周杰伦")
    incoming(songs, "唱周杰伦的稻香", event="new")
    await songs.engine.reply("12345")
    await songs.manager.tick()
    assert "游戏模式" in songs.sent[-1][1] and not songs.started


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["forget", "identity", "delete", "cancel"])
async def test_pending_song_obeys_forget_identity_deletion_and_cancellation(songs, change):
    songs.parsed.update(title="稻香", artist="周杰伦")
    incoming(songs, "唱周杰伦的稻香")
    await songs.engine.reply("12345")
    song = songs.manager.library.list()[0]
    file = songs.manager.library.audio_path(song["id"]).resolve()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(b"audio")
    songs.store.db.execute("UPDATE songs SET state='ready',file=? WHERE id=?", (str(file), song["id"]))
    if change == "forget":
        songs.store.forget("12345")
    elif change == "identity":
        songs.cfg["bot_qq"] = "22222"
    elif change == "delete":
        songs.manager.library.delete(song["id"])
    else:
        incoming(songs, "不听了", event="cancel")
        await songs.engine.reply("12345")
    await songs.manager.tick()
    assert not any(s[2].get("song_file") for s in songs.sent)
    assert songs.store.db.execute("SELECT state FROM song_requests").fetchone()[0] == "cancelled"


@pytest.mark.asyncio
async def test_personal_and_global_message_limits_still_apply(songs):
    ready(songs)
    songs.cfg["guest_daily_messages"] = 1
    songs.store.action("prior", "11111", "reply", ["old"])
    songs.store.outcome("prior", "succeeded")
    incoming(songs, "唱司凤版雨爱", "11111")
    await songs.engine.reply("11111")
    assert not songs.store.db.execute("SELECT 1 FROM song_requests").fetchone()
    assert not any(s[2].get("song_file") for s in songs.sent)


@pytest.mark.asyncio
async def test_group_song_uses_original_group_and_native_record_segment(songs):
    song = ready(songs)
    contact = "group:99999"
    songs.store.incoming("group-event", contact, "[群成员 A] 唱司凤版雨爱")
    songs.store.set("song_origin:" + contact, dict(event_id="group-event", text="唱司凤版雨爱", requester="11111", created=time.time()))
    await songs.groups.engine.reply(contact)
    assert songs.native[-1][0] == "send_group_msg"
    assert songs.native[-1][1] == dict(group_id=99999, message=[dict(type="record", data=dict(file=Path(song["file"]).as_uri()))])
    assert not songs.sent


@pytest.mark.asyncio
async def test_group_artist_followup_is_only_for_the_original_requester(songs):
    ready(songs)

    async def drain(contact):
        pass

    songs.groups.drain = drain
    raw = dict(group_id=99999, user_id=11111, message_id=1, message=[dict(type="text", data=dict(text="唱雨爱")),
               dict(type="at", data=dict(qq="67890"))])
    await songs.groups.receive(raw)
    await asyncio.gather(*songs.groups.tasks.values())
    songs.groups.tasks.clear()
    await songs.groups.engine.reply("group:99999")
    assert "哪位歌手" in songs.native[-1][1]["message"][0]["data"]["text"]
    raw.update(user_id=22222, message_id=2, message=[dict(type="text", data=dict(text="司凤版"))])
    await songs.groups.receive(raw)
    await asyncio.gather(*songs.groups.tasks.values())
    songs.groups.tasks.clear()
    assert songs.store.get("song_origin:group:99999")["requester"] == "11111"
    raw.update(user_id=11111, message_id=3)
    await songs.groups.receive(raw)
    await asyncio.gather(*songs.groups.tasks.values())
    songs.groups.tasks.clear()
    await songs.groups.engine.reply("group:99999")
    assert songs.native[-1][1]["message"][0]["type"] == "record"
    assert songs.store.db.execute("SELECT requester FROM song_requests").fetchone()[0] == "11111"


@pytest.mark.asyncio
async def test_ready_request_survives_new_controller_without_resending_completed_song(songs):
    songs.parsed.update(title="稻香", artist="周杰伦")
    incoming(songs, "唱周杰伦的稻香")
    await songs.engine.reply("12345")
    song = songs.manager.library.list()[0]
    file = songs.manager.library.audio_path(song["id"]).resolve()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(b"audio")
    songs.store.db.execute("UPDATE songs SET state='ready',file=? WHERE id=?", (str(file), song["id"]))
    restarted = Singing(songs.plugin, root=songs.root, config_reader=lambda: songs.cfg)
    await restarted.tick()
    await restarted.tick()
    assert len([s for s in songs.sent if s[2].get("song_file")]) == 1


def test_catalogue_preview_and_delete_never_access_user_paths(songs, monkeypatch):
    song = ready(songs)
    monkeypatch.setattr(desktop_api, "ROOT", songs.root)
    api = desktop_api.DesktopAPI()
    assert api.get_songs()["data"]["songs"][0]["id"] == song["id"]
    assert "file" not in api.get_songs()["data"]["songs"][0]
    assert api.song_preview(song["id"])["data"].startswith("data:audio/mpeg;base64,")
    protected = songs.root / "my-song.mp3"
    protected.write_bytes(b"user original")
    assert api.delete_song(song["id"])["data"]["deleted"]
    assert not Path(song["file"]).exists() and protected.exists()
    assert not api.song_preview(song["id"])["ok"]
    assert not api.delete_song("../../my-song.mp3")["data"]["deleted"]


def test_delete_while_learning_stops_only_owned_job_and_does_not_restore_entry(songs):
    song = songs.manager.library.add("稻香", "周杰伦")
    songs.manager.start_worker(song)
    work = songs.manager.library.directory / "work" / song["id"]
    work.mkdir(parents=True)
    (work / "temporary.wav").write_bytes(b"scratch")
    songs.manager.library.delete(song["id"])
    songs.manager.poll()
    assert not songs.alive and not work.exists()
    assert songs.manager.library.get(song["id"])["state"] == "deleted"
    assert not songs.manager.library.list()


@pytest.mark.asyncio
async def test_deleted_alias_can_be_requested_again_without_guessing_artist(songs):
    song = ready(songs)
    songs.manager.library.delete(song["id"])
    songs.parsed.update(title="雨爱", artist="司凤")
    incoming(songs, "唱司冯版雨爱")
    await songs.engine.reply("12345")
    assert "做好后" in songs.sent[-1][1]
    assert songs.manager.library.list()[0]["id"] != song["id"]


@pytest.mark.asyncio
async def test_worker_start_failure_is_reported_instead_of_waiting_forever(songs, monkeypatch):
    songs.parsed.update(title="稻香", artist="周杰伦")
    incoming(songs, "唱周杰伦的稻香")
    await songs.engine.reply("12345")

    def unavailable(song):
        raise FileNotFoundError("missing isolated python")

    monkeypatch.setattr(songs.manager, "start_worker", unavailable)
    await songs.manager.tick()
    await songs.manager.tick()
    assert songs.manager.library.list()[0]["state"] == "failed"
    assert "无法启动" in songs.sent[-1][1]
    assert songs.store.db.execute("SELECT state FROM song_requests").fetchone()[0] == "failed"


def test_source_selection_requires_matching_title_and_artist(songs, monkeypatch):
    def api(path, args):
        if "search" in path:
            return dict(result=[dict(title="雨爱 杨丞琳", bvid="wrong"), dict(title="雨爱（司凤版）", bvid="BV1i2h96bEwj")])
        return dict(title="雨爱（司凤版）", duration=102, cid=1, pages=[{}], owner={})

    monkeypatch.setattr(song_source, "api", api)
    assert song_source.resolve("雨爱", "司凤")["bvid"] == "BV1i2h96bEwj"
    with pytest.raises(ValueError):
        song_source.resolve("雨爱", "不存在的歌手")
    with pytest.raises(ValueError):
        song_source.video_id("http://127.0.0.1/private")


@pytest.mark.asyncio
async def test_private_adapter_sends_song_as_record_for_friend(songs, monkeypatch):
    song = ready(songs)
    sys.path.insert(0, str(ROOT / "astrbot"))
    os.environ["QQCOMPANION_ROOT"] = str(ROOT)
    spec = importlib.util.spec_from_file_location("singing_adapter_test", ROOT / "plugins/astrbot_plugin_qq_companion/main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "read_settings", lambda: songs.cfg)
    plugin = module.QQCompanion.__new__(module.QQCompanion)
    plugin.check_messaging = songs.plugin.check_account
    plugin.bot, plugin.singing = songs.plugin.bot, songs.manager
    await plugin.send_private("11111", "caption", song_file=song["file"], song_id=song["id"])
    assert songs.native[-1][1] == dict(user_id=11111, message=[dict(type="record", data=dict(file=Path(song["file"]).as_uri()))])


def test_cleanup_refuses_paths_outside_song_workspace(songs):
    with pytest.raises(ValueError):
        songs.manager.cleanup("../../user-files")
    assert songs.root.exists()


@pytest.mark.parametrize("override,expected", [(None, "1"), ("0", "0")])
def test_installed_singing_models_use_cache_before_network(tmp_path, monkeypatch, override, expected):
    path = ROOT / "integrations/seedvc/companion_svc.py"
    spec = importlib.util.spec_from_file_location("singing_cached_models_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    local = tmp_path / "SeedVC"
    local.mkdir()
    source, target = local / "vocals.wav", local / "reference.wav"
    source.write_bytes(b"source")
    target.write_bytes(b"target")
    environment = dict(os.environ)
    environment.pop("HF_HUB_OFFLINE", None)
    if override is not None:
        environment["HF_HUB_OFFLINE"] = override
    monkeypatch.setattr(module.os, "environ", environment)
    monkeypatch.setattr(module, "ROOT", local)
    monkeypatch.setattr(sys, "argv", [str(path), "--source", str(source), "--target", str(target)])
    monkeypatch.chdir(tmp_path)
    converted = []

    def convert(args):
        assert os.environ["HF_HUB_OFFLINE"] == expected
        converted.append(args)

    monkeypatch.syspath_prepend(str(path.parent))
    monkeypatch.setitem(sys.modules, "inference", SimpleNamespace(main=convert, load_models=lambda args: (),
                        sf=SimpleNamespace(write=lambda *args, **kwargs: None)))
    monkeypatch.setitem(sys.modules, 'modules', SimpleNamespace(
        length_regulator=SimpleNamespace(f0_to_coarse=lambda f0, bins: f0)))
    module.main()
    assert len(converted) == 1
    assert converted[0].f0_condition and converted[0].diffusion_steps == 50


def test_bilibili_temporary_block_retries_in_same_session(monkeypatch):
    replies = [SimpleNamespace(status_code=412), SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                                                               json=lambda: dict(code=0, data=dict(result=[])))]
    calls, sleeps = [], []
    def get(url, **kwargs):
        calls.append(url)
        return replies.pop(0)
    monkeypatch.setattr(song_source, "session", lambda: SimpleNamespace(get=get))
    monkeypatch.setattr(song_source.time, "sleep", sleeps.append)
    assert song_source.api("/x/web-interface/search/type", {}) == dict(result=[])
    assert len(calls) == 2 and sleeps == [3]


def test_persistent_block_reports_search_stage_without_raw_response(monkeypatch):
    monkeypatch.setattr(song_source, "session", lambda: SimpleNamespace(get=lambda *a, **k: SimpleNamespace(status_code=412)))
    monkeypatch.setattr(song_source.time, "sleep", lambda seconds: None)
    with pytest.raises(song_source.SourceTemporary, match="B站搜索暂时被限制") as e:
        song_source.api("/x/web-interface/search/type", {})
    assert e.value.stage == "search"


def test_backup_and_automatic_source_must_match_actual_video_metadata(monkeypatch):
    monkeypatch.setattr(song_source, "api", lambda *args: dict(title="无关的歌", duration=180, cid=1, pages=[{}]))
    with pytest.raises(song_source.SourceNeeded, match="没有明确对应"):
        song_source.resolve("time machine", "mj apanay", "https://www.bilibili.com/video/BV1faak6AEzm/", verify_source=True)


def finish_with_error(env, state, stage="search"):
    song = env.manager.library.list()[0]
    env.manager.start_worker(song)
    env.alive.clear()
    work = env.manager.library.directory / "work" / song["id"]
    work.mkdir(parents=True, exist_ok=True)
    atomic_json(work / "result.json", dict(state=state, error="B站搜索暂时被限制（412）。", stage=stage))
    env.manager.poll()
    return env.manager.library.get(song["id"])


@pytest.mark.asyncio
async def test_temporary_source_failure_keeps_request_and_durable_cooldown(songs):
    songs.parsed.update(title="time machine", artist="mj apanay")
    incoming(songs, "唱mj apanay的time machine")
    await songs.engine.reply("12345")
    song = finish_with_error(songs, "retry")
    assert song["state"] == "queued"
    assert songs.store.get("song_retry:" + song["id"])["due"] > time.time()
    before = len(songs.started)
    await songs.manager.tick()
    assert len(songs.started) == before
    assert "稍后自动再试" in songs.sent[-1][1]
    assert songs.store.db.execute("SELECT state FROM song_requests").fetchone()[0] == "waiting"
    fresh = Singing(songs.plugin, root=songs.root, config_reader=lambda: songs.cfg)
    assert fresh.store.get("song_retry:" + song["id"])["attempts"] == 1


@pytest.mark.asyncio
async def test_bounded_source_retries_then_wait_for_link_without_losing_request(songs):
    songs.parsed.update(title="time machine", artist="mj apanay")
    incoming(songs, "唱mj apanay的time machine")
    await songs.engine.reply("12345")
    for i in range(4):
        song = finish_with_error(songs, "retry")
    assert song["state"] == "needs_source"
    await songs.manager.tick()
    assert songs.store.db.execute("SELECT state FROM song_requests").fetchone()[0] == "awaiting_source"
    assert "补一条B站单曲链接" in songs.sent[-1][1]
    request_id = songs.store.db.execute("SELECT id FROM song_requests").fetchone()[0]
    model_calls = len(songs.model_calls)
    incoming(songs, "用这个 https://www.bilibili.com/video/BV1faak6AEzm/?spm_id=test", event="link")
    await songs.engine.reply("12345")
    assert len(songs.model_calls) == model_calls
    assert songs.store.db.execute("SELECT COUNT(*) FROM song_requests").fetchone()[0] == 1
    request = songs.store.db.execute("SELECT id,state,song_id FROM song_requests").fetchone()
    assert request["id"] == request_id and request["state"] == "waiting"
    updated = songs.manager.library.get(request["song_id"])
    assert updated["source_url"] == "https://www.bilibili.com/video/BV1faak6AEzm/"
    assert updated["state"] == "queued" and not songs.store.get("song_retry:" + song["id"])


@pytest.mark.asyncio
async def test_backup_search_uses_existing_budgeted_lookup_once_and_checks_title(songs):
    songs.cfg["web_search"] = True
    songs.parsed.update(title="time machine", artist="mj apanay")
    incoming(songs, "唱mj apanay的time machine")
    await songs.engine.reply("12345")
    song = finish_with_error(songs, "retry")
    queries = []
    async def lookup(query, cfg, contact):
        queries.append(query)
        return dict(sources=[dict(title="杨丞琳 雨爱", url="https://www.bilibili.com/video/BV1i2h96bEwj/"),
                             dict(title="time machine - mj apanay 完整版", url="https://www.bilibili.com/video/BV1faak6AEzm/")])
    songs.engine.web.lookup = lookup
    await songs.manager.tick()
    retry = songs.store.get("song_retry:" + song["id"])
    assert retry["backup_url"] == "https://www.bilibili.com/video/BV1faak6AEzm/" and retry["due"] == 0
    assert songs.started[-1] == song["id"] and len(queries) == 1
    await songs.manager.backup_search(song, songs.cfg)
    assert len(queries) == 1 and "12345" not in queries[0]


def test_explicit_link_never_silently_uses_another_cached_version(songs):
    old = ready(songs)
    new = songs.manager.library.add("雨爱", "司冯", "https://www.bilibili.com/video/BV1faak6AEzm/")
    assert new["id"] != old["id"] and new["state"] == "queued"
    assert songs.manager.library.get(old["id"])["state"] == "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["delete", "forget", "cancel"])
async def test_waiting_for_source_can_be_cancelled_or_forgotten(songs, operation):
    songs.parsed.update(title="time machine", artist="mj apanay")
    incoming(songs, "唱mj apanay的time machine")
    await songs.engine.reply("12345")
    song = finish_with_error(songs, "needs_source")
    await songs.manager.tick()
    if operation == "delete":
        songs.manager.library.delete(song["id"])
    elif operation == "forget":
        songs.store.forget("12345")
    else:
        incoming(songs, "取消点歌", event="cancel-source")
        await songs.engine.reply("12345")
    assert songs.store.db.execute("SELECT state FROM song_requests").fetchone()[0] == "cancelled"


@pytest.mark.asyncio
async def test_unspecified_song_then_delegated_choice_creates_real_send(songs):
    song = ready(songs)
    songs.parsed.update(intent="request", title="", artist="")
    incoming(songs, "给我唱一首歌", contact="11111", event="open-song")
    await songs.engine.reply("11111")
    key = "song_pending:67890:11111:11111"
    assert songs.store.get(key)["stage"] == "title"
    assert not songs.store.db.execute("SELECT 1 FROM song_requests").fetchone()
    songs.parsed.update(intent="choose", song_id=song["id"])
    incoming(songs, "你想给我唱什么都OK", contact="11111", event="choose-song")
    await songs.engine.reply("11111")
    assert songs.sent[-1][0] == "11111" and songs.sent[-1][2]["song_id"] == song["id"]
    row = songs.store.db.execute("SELECT contact,state FROM song_requests").fetchone()
    assert row["contact"] == "11111" and row["state"] == "sent"
    assert not songs.store.get(key)
    payload = json.loads(songs.model_calls[-1][1][0]["content"])
    assert payload["awaiting_song"] and payload["ready_songs"][0]["id"] == song["id"]


@pytest.mark.asyncio
async def test_choice_followup_without_singing_keyword_is_not_dropped(songs):
    song = ready(songs)
    songs.parsed.update(intent="request", title="", artist="")
    incoming(songs, "给我唱一首歌", event="ask-any")
    await songs.engine.reply("12345")
    songs.parsed.update(intent="choose", song_id=song["id"])
    incoming(songs, "随便，你来选吧", event="delegate")
    await songs.engine.reply("12345")
    assert songs.sent[-1][2].get("song_file")
    before = len(songs.sent)
    await songs.manager.handle("12345", songs.engine)
    assert len(songs.sent) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("catalog", [False, True])
async def test_delegation_never_invents_uncached_choice_or_promises_progress(songs, catalog):
    if catalog:
        ready(songs)
    songs.parsed.update(intent="choose", song_id="invented-song")
    incoming(songs, "随便唱一首")
    await songs.engine.reply("12345")
    assert not songs.store.db.execute("SELECT 1 FROM song_requests").fetchone()
    assert not songs.started and not songs.sent[-1][2]
    assert "没选定" in songs.sent[-1][1] or "还没有准备好" in songs.sent[-1][1]


@pytest.mark.asyncio
async def test_hurry_without_task_reports_selection_not_fake_preparation(songs):
    ready(songs)
    songs.parsed.update(intent="request", title="", artist="")
    incoming(songs, "给我唱一首歌", event="open")
    await songs.engine.reply("12345")
    assert songs.manager.needs_context("12345")
    songs.parsed["intent"] = "status"
    incoming(songs, "您能快点嘛", event="hurry")
    await songs.engine.reply("12345")
    assert "还没开始制作或发送" in songs.sent[-1][1]
    assert not songs.store.db.execute("SELECT 1 FROM song_requests").fetchone()


@pytest.mark.asyncio
async def test_progress_uses_actual_job_and_current_requester(songs):
    songs.parsed.update(intent="request", title="稻香", artist="周杰伦")
    incoming(songs, "唱周杰伦的稻香", contact="11111", event="job")
    await songs.engine.reply("11111")
    songs.store.db.execute("UPDATE songs SET state='processing',progress='查找完整音源'")
    songs.parsed["intent"] = "status"
    incoming(songs, "能快点吗", contact="11111", event="progress")
    await songs.engine.reply("11111")
    assert "查找完整音源" in songs.sent[-1][1] and "还没发出" in songs.sent[-1][1]
    assert not songs.manager.latest_request("11111", "12345")


@pytest.mark.asyncio
async def test_cancel_open_selection_does_not_start_a_song(songs):
    ready(songs)
    songs.parsed.update(intent="request", title="", artist="")
    incoming(songs, "给我唱首歌", event="ask")
    await songs.engine.reply("12345")
    incoming(songs, "不听了", event="no-song")
    await songs.engine.reply("12345")
    assert not songs.store.get("song_pending:67890:12345:12345")
    assert not songs.store.db.execute("SELECT 1 FROM song_requests").fetchone()


@pytest.mark.asyncio
async def test_delegating_version_of_named_song_does_not_choose_a_different_song(songs):
    original = ready(songs)
    other = ready(songs, title="time machine", artist="mj apanay", aliases=())
    songs.store.set("song_pending:67890:12345:12345", dict(title=original["title"], expires=time.time()+3600))
    songs.parsed.update(intent="choose", song_id=other["id"])
    incoming(songs, "随便哪个版本都可以")
    await songs.engine.reply("12345")
    assert not songs.store.db.execute("SELECT 1 FROM song_requests").fetchone()
    payload = json.loads(songs.model_calls[-1][1][0]["content"])
    assert [s["id"] for s in payload["ready_songs"]] == [original["id"]]
