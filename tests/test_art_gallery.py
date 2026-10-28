# ruff: noqa: E402
import base64
import json
import sqlite3
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import desktop_api
from art_gallery import ArtGallery
from sketchbook import parse_scene, render_scene, Sketchbook
from store import Store


def test_whale_variants_change_shape_and_pose_without_detaching_parts(tmp_path):
    images = []
    for index, options in enumerate([{}, dict(shape='slender'), dict(pose='up', fin='wave'), dict(pose='down')]):
        scene = parse_scene(json.dumps(dict(action='draw', self_portrait=True, title='鲸鱼', caption='各不相同',
            elements=[dict(type='whale', box=[100,100,900,900], **options)])))
        path = tmp_path / f'{index}.png'
        render_scene(scene, path)
        images.append(path.read_bytes())
    assert len(set(images)) == 4


def test_self_portrait_can_use_its_own_connected_curve_group():
    body = dict(type='bezier', closed=True, points=[[100,400],[100,100],[900,100],[900,400],[900,800],[100,800],[100,400]], fill='#75bdea')
    face = [dict(type='ellipse', box=[650,350,690,400]), dict(type='line',points=[[680,450],[730,460]])]
    scene = parse_scene(json.dumps(dict(action='draw',self_portrait=True,title='自由鲸鱼',caption='新的轮廓',
        elements=[dict(type='group',box=[100,100,900,900],elements=[body,*face])])))
    assert scene['elements'][0]['elements'][0] == body


def test_connected_whale_renders_inside_its_group_with_visible_shading(tmp_path):
    scene = parse_scene(json.dumps(dict(action='draw', self_portrait=True, title='鲸鱼', caption='安静的小鲸鱼',
        background='#ffffff', elements=[dict(type='whale', box=[100,200,900,800], facing='left', mood='开心')]), ensure_ascii=False))
    file = tmp_path / 'whale.png'
    render_scene(scene, file)
    image = Image.open(file).convert('RGB')
    assert image.size == (960, 640)
    assert image.getpixel((20, 20)) == (255,255,255)
    # The body is visible and a range of blue shades is actually rendered.
    blues = {pixel for pixel in image.getdata() if pixel[2] > pixel[0] + 40 and pixel[1] > pixel[0]}
    assert len(blues) > 40


def test_self_portrait_cannot_fall_back_to_disconnected_humanoid_parts():
    with pytest.raises(ValueError, match='connected whale'):
        parse_scene(json.dumps(dict(action='draw', self_portrait=True, title='画自己', caption='头像', elements=[
            dict(type='ellipse',box=[100,100,300,300]), dict(type='ellipse',box=[500,500,800,800]),
            dict(type='line',points=[[100,400],[200,500]])])))


@pytest.fixture
def art(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    store = Store(tmp_path / "data/companion.sqlite3")
    cfg = dict(bot_qq="67890", owner_qq="12345")
    monkeypatch.setattr(desktop_api, "ROOT", tmp_path)
    monkeypatch.setattr(desktop_api, "read_settings", lambda: cfg)
    yield SimpleNamespace(root=tmp_path, store=store, cfg=cfg, gallery=ArtGallery(store.db, "67890", tmp_path), api=desktop_api.DesktopAPI())
    store.db.close()


def add(art, scope="public", title="午后小画", bot="67890"):
    ident = uuid.uuid4().hex
    file = art.root / "data/drawings" / bot / scope / (ident + ".png")
    file.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (96, 64), "#afcddd").save(file)
    file.with_suffix(".json").write_text('{"action":"draw"}', encoding="utf-8")
    row = dict(id=ident, file=str(file), scope=scope, title=title, caption="一点开心", created=1)
    key = f"sketches:{bot}:{scope}"
    art.store.set(key, art.store.get(key, []) + [row])
    return row


def test_gallery_lists_real_current_account_art_and_preserves_scope(art):
    add(art)
    private = add(art, "12345", "送给你的鲸鱼")
    add(art, bot="22222", title="另一个账号")
    data = art.api.get_drawings()["data"]
    assert data["total"] == 2 and data["public_count"] == data["private_count"] == 1
    assert all(row["thumbnail"].startswith("data:image/png;base64,") and "file" not in row for row in data["drawings"])
    filtered = art.api.get_drawings("鲸鱼", "private")["data"]
    assert [row["id"] for row in filtered["drawings"]] == [private["id"]]
    assert not art.api.drawing_preview(private["id"], "public")["ok"]


def test_gallery_preview_and_delete_remove_only_registered_png_and_draft(art):
    public = add(art)
    private = add(art, "12345")
    data = art.api.drawing_preview(public["id"], "public")["data"]
    assert base64.b64decode(data.split(",", 1)[1]) == Path(public["file"]).read_bytes()
    assert art.api.delete_drawing(public["id"], "public")["data"]["deleted"]
    assert not Path(public["file"]).exists() and not Path(public["file"]).with_suffix(".json").exists()
    assert Path(private["file"]).exists()
    book = Sketchbook(art.store, art.root)
    assert not book.allowed_file(art.cfg, public["file"])
    assert not book.pending_public(art.cfg)
    assert art.api.get_drawings()["data"]["total"] == 1


def test_gallery_refuses_unregistered_paths_and_forgotten_private_art(art):
    row = add(art, "12345")
    art.store.forget("12345")
    assert Path(row["file"]).is_file() and not art.gallery.entries()
    outside = art.root / "original.png"
    Image.new("RGB", (2, 2)).save(outside)
    art.store.set("sketches:67890:public", [dict(row, scope="public", file=str(outside))])
    assert not art.api.drawing_preview(row["id"], "public")["ok"]
    assert not art.api.delete_drawing(row["id"], "public")["data"]["deleted"]
    assert outside.exists()


def test_gallery_save_respects_file_dialog_and_cancel(art, monkeypatch):
    row = add(art)
    destination = art.root / "chosen.png"
    chosen = [None, str(destination)]
    calls = []
    def dialog(*args, **kwargs):
        calls.append(kwargs)
        return chosen.pop(0)
    art.api._window = SimpleNamespace(create_file_dialog=dialog)
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(SAVE_DIALOG=123))
    assert not art.api.save_drawing(row["id"], "public")["data"]["saved"]
    assert not destination.exists()
    assert art.api.save_drawing(row["id"], "public")["data"]["saved"]
    assert destination.read_bytes() == Path(row["file"]).read_bytes()
    assert calls[0]["save_filename"].endswith(".png")


def test_gallery_is_read_only_and_pages_24_at_a_time(art):
    for i in range(25):
        add(art, title=f"第{i}幅")
    with sqlite3.connect((art.root / "data/companion.sqlite3").as_uri() + "?mode=ro", uri=True) as db:
        page = ArtGallery(db, "67890", art.root).page(page=2)
    assert page["pages"] == 2 and page["total"] == 25 and len(page["drawings"]) == 1


def enhanced_scene():
    return dict(action="draw", title="小鱼", caption="弯弯的轮廓", background="#f8f1df", background2="#dfece5", elements=[
        dict(type="bezier", points=[[200,300],[200,100],[800,100],[800,500],[800,800],[200,800],[200,300]], closed=True, fill="#afcad5", fill2="#74a9c1", stroke="#324858"),
        dict(type="rounded_rect", box=[80,750,180,900], fill="#debba0", stroke=None),
        dict(type="line", points=[[300,400],[500,450]], stroke="#324858", width=5)])


def test_enhanced_primitives_render_complete_gradient_and_curved_png(tmp_path):
    file = tmp_path / "curves.png"
    render_scene(parse_scene(json.dumps(enhanced_scene())), file)
    with Image.open(file) as image:
        assert image.size == (960, 640)
        assert image.getpixel((0,0)) != image.getpixel((0,639))
        assert len(image.getcolors(maxcolors=1_000_000)) > 500


@pytest.mark.parametrize("change", [
    lambda s: s["elements"][0].update(points=[[1,2],[3,4],[5,6]]),
    lambda s: s["elements"][0].update(fill2="url(secret)"),
    lambda s: s["elements"][0].update(closed="yes"),
    lambda s: s.update(background2="not-a-color"),
])
def test_new_drawing_options_still_validate_bounded_input(change):
    scene = enhanced_scene()
    change(scene)
    with pytest.raises(ValueError):
        parse_scene(json.dumps(scene))


def grouped_scene():
    return dict(action="draw", title="头朝左的小鲸鱼", caption="五官跟着头部放。", elements=[
        dict(type="ellipse", box=[200,300,800,800], fill="#91bcd1"),
        dict(type="group", box=[230,400,400,650], elements=[
            dict(type="line", points=[[200,300],[400,300]], stroke="#324858"),
            dict(type="line", points=[[100,650],[350,750]], stroke="#324858")])])


def test_grouped_facial_parts_move_together_with_head_frame(tmp_path):
    from sketchbook import flatten_elements
    scene = grouped_scene()
    first = flatten_elements(scene["elements"])
    scene["elements"][1]["box"] = [330,450,500,700]
    moved = flatten_elements(scene["elements"])
    assert moved[0] == first[0]
    for before, after in zip(first[1:], moved[1:]):
        for p, q in zip(before["points"], after["points"]):
            assert q[0] - p[0] == pytest.approx(100)
            assert q[1] - p[1] == pytest.approx(50)
    file = tmp_path / "grouped.png"
    render_scene(parse_scene(json.dumps(scene)), file)
    with Image.open(file) as image:
        assert image.size == (960,640)


@pytest.mark.parametrize("problem", ["out_of_frame", "too_many", "too_deep"])
def test_group_coordinates_and_total_complexity_remain_bounded(problem):
    scene = grouped_scene()
    group = scene["elements"][1]
    if problem == "out_of_frame":
        group["elements"][0]["points"][0][0] = -100
    elif problem == "too_many":
        group["elements"] *= 60
    else:
        for _ in range(4):
            group["elements"] = [dict(type="group", box=[0,0,1000,1000], elements=group["elements"])]
    with pytest.raises(ValueError):
        parse_scene(json.dumps(scene))
