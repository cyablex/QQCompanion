"""Model-composed drawings, rendered locally from bounded geometric primitives."""

import asyncio
import json
import math
import re
import time
import uuid
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

from personality import build_personality
from public_novelty import history_context, is_novel, recent_public
from settings import ROOT, atomic_json
from store import TZ

MOODS = {"开心", "好奇", "专注", "平静", "低落", "困倦"}
DRAWING_MARK = re.compile(r"\[画画[:：]([^\]\r\n]{1,200})\]")
MOOD_MARK = re.compile(r"\[心情[:：](开心|好奇|专注|平静|低落|困倦)\]")

SCENE_GUIDE = """你会用几何图形自由构图画简笔画，不是挑一张固定模板。
只输出 JSON：{"action":"draw","title":"作品名","caption":"分享时的一两句自然文字",
"mood":"开心","background":"#f5f0e5","elements":[
{"type":"ellipse","box":[100,200,400,500],"fill":"#75bdea","stroke":"#243a51","width":3},
{"type":"text","at":[100,100],"text":"小鱼的宇宙","size":35,"fill":"#243a51"}]}。
也可输出 {"action":"skip"}。坐标以左上角为原点，横纵均为0至1000；画布实际为960×640。
元素3至100个，按绘制顺序叠加：ellipse/rect/rounded_rect 用 box=[左,上,右,下]；
可用group：{"type":"group","box":[左,上,右,下],"elements":[图元...]}，内部坐标0至1000相对于该框，最多嵌套3层。总图元仍不超过100个，线宽和字号仍用原单位。
polygon 用 points=[[x,y],...]；line 用 points；arc 用 box、start、end（角度）；
bezier 用4、7、10至25个points，每3个点为一段三次曲线的两个控制点和终点，closed=true可闭合填色。
text 用 at=[x,y]、text（最多30字）、size（12至70）。图形可用fill、stroke和width（1至16）。
text的at是文字左上角，不是基线；文字高度大约占size×1.5个纵向坐标单位，牌子中的文字要留足上下边距。
颜色为#RRGGBB；无需填充用fill=null。线条使用stroke。可选background2作背景渐变，图形fill2作柔和渐变，shadow=false关阴影；style可选soft（默认）或flat。只有这些图元，不输出代码、SVG或网址。
画画可以只是因为自己想画、看着舒服或心情开心，不需要知识点、研究进展、寓意或笑点。
可以画自己在想象中的太阳底下躺着、两只开心的鲸鱼、小动物、食物、植物、抽象心情或有趣的小场景；
不固定模板，不必每次换成一个科普课题。构图留白，主体有表情和动作，文字少且避开主体。标题不必再画在图里。
适当用平滑曲线表现轮廓、少量前后层次和协调配色，不为丰富细节堆图元；渐变阴影按需要用，不必每个图元都加字段。
先明确头尾朝向与头部位置，再画五官；眼睛、嘴和腮红放进同一个头部group，与身体一起放进角色group，避免各自猜全图坐标。侧脸五官靠头端，不靠尾根；嘴在眼睛下方并略靠脸朝向的一侧。鱼鳍、四肢从身体对应处连接，别像独立贴片；装饰线、腹线不要穿过五官或像第二张嘴，拿不准就省掉。遮挡按后面的部件先画、前面的后画。输出前在同一轮检查部件位置与连接关系，不额外增加一轮调用。
caption不超过120字，只描述这幅作品或创作想法，不捏造现实经历。mood限开心/好奇/专注/平静/低落/困倦。
另加composition字段，用最多160字说明画面主体、动作、关系和氛围，供记住构图，不能只写颜色和几何图元。
"""


def parse_scene(raw):
    value = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
    if not isinstance(value, dict) or value.get("action") not in {"draw", "skip"}:
        raise ValueError("Invalid drawing decision")
    if value["action"] == "skip":
        return value
    if not all(isinstance(value.get(k), str) and value[k].strip() for k in ("title", "caption")):
        raise ValueError("Drawing needs a title and caption")
    elements = flatten_elements(value.get("elements"))
    if not 3 <= len(elements) <= 100:
        raise ValueError("Invalid drawing element count")
    color = re.compile(r"#[0-9a-fA-F]{6}\Z")
    if not color.fullmatch(value.get("background", "#fffaf0")):
        raise ValueError("Invalid canvas color")
    if value.get("background2") is not None and not color.fullmatch(str(value["background2"])):
        raise ValueError("Invalid canvas gradient")
    if value.get("style", "soft") not in {"soft", "flat"}:
        raise ValueError("Invalid drawing style")
    for item in elements:
        if not isinstance(item, dict) or item.get("type") not in {"ellipse", "rect", "rounded_rect", "polygon", "line", "arc", "text", "bezier"}:
            raise ValueError("Unsupported drawing primitive")
        for key in ("fill", "fill2", "stroke"):
            if item.get(key) is not None and not color.fullmatch(str(item[key])):
                raise ValueError("Invalid drawing color")
        kind = item["type"]
        if any(key in item and type(item[key]) is not bool for key in ("closed", "shadow")):
            raise ValueError("Invalid drawing flag")
        coords = item.get("at") if kind == "text" else item.get("points") if kind in {"polygon", "line", "bezier"} else item.get("box")
        if kind in {"polygon", "line", "bezier"}:
            if not isinstance(coords, list) or not 2 <= len(coords) <= 80 or any(not isinstance(p, list) or len(p) != 2 for p in coords):
                raise ValueError("Invalid drawing points")
            if kind == "bezier" and (not 4 <= len(coords) <= 25 or (len(coords) - 1) % 3):
                raise ValueError("Invalid Bezier control points")
            coords = [v for p in coords for v in p]
        if not isinstance(coords, list) or (kind == "text" and len(coords) != 2) or (kind not in {"text", "polygon", "line", "bezier"} and len(coords) != 4):
            raise ValueError("Invalid drawing coordinates")
        if any(type(v) not in {int, float} or not math.isfinite(v) or not 0 <= v <= 1000 for v in coords):
            raise ValueError("Drawing coordinates out of range")
        if kind not in {"text", "polygon", "line", "bezier"} and (coords[0] >= coords[2] or coords[1] >= coords[3]):
            raise ValueError("Drawing box must have positive size")
        if kind == "text" and (not isinstance(item.get("text"), str) or len(item["text"]) > 30):
            raise ValueError("Invalid drawing label")
        for key, default, low, high in (("width", 3, 1, 16), ("size", 30, 12, 70), ("start", 0, -360, 360), ("end", 360, -360, 720)):
            number = item.get(key, default)
            if type(number) not in {int, float} or not math.isfinite(number) or not low <= number <= high:
                raise ValueError("Drawing numeric option out of range")
    value.update(title=value["title"][:60], caption=value["caption"][:120])
    return value


def flatten_elements(elements, bounds=(0, 0, 1000, 1000), depth=0):
    """Place grouped parts in their parent's coordinates without changing shape."""
    if not isinstance(elements, list) or not 1 <= len(elements) <= 100 or depth > 3:
        raise ValueError("Invalid drawing group")
    left, top, right, bottom = bounds

    def transform(coords):
        if (not isinstance(coords, list) or len(coords) not in {2, 4}
                or any(type(v) not in {int, float} or not math.isfinite(v) or not 0 <= v <= 1000 for v in coords)):
            raise ValueError("Invalid local drawing coordinates")
        return [left + value * (right - left) / 1000 if i % 2 == 0 else top + value * (bottom - top) / 1000
                for i, value in enumerate(coords)]

    result = []
    for item in elements:
        if not isinstance(item, dict):
            raise ValueError("Invalid drawing element")
        if item.get("type") == "group":
            box = transform(item.get("box"))
            if len(box) != 4 or box[0] >= box[2] or box[1] >= box[3]:
                raise ValueError("Invalid drawing group bounds")
            result.extend(flatten_elements(item.get("elements"), box, depth + 1))
        else:
            placed = dict(item)
            for field in ("box", "at"):
                if field in placed:
                    placed[field] = transform(placed[field])
            if "points" in placed:
                if not isinstance(placed["points"], list) or len(placed["points"]) > 80:
                    raise ValueError("Invalid drawing points")
                placed["points"] = [transform(p) for p in placed["points"]]
            result.append(placed)
        if len(result) > 100:
            raise ValueError("Too many drawing primitives")
    return result


def gradient(size, top, bottom):
    mask = Image.linear_gradient("L").resize(size)
    return Image.composite(Image.new("RGB", size, bottom), Image.new("RGB", size, top), mask)


def bezier_points(points):
    result = [points[0]]
    for start in range(0, len(points) - 1, 3):
        a, b, c, d = points[start:start + 4]
        for step in range(1, 41):
            t = step / 40
            result.append(tuple(round((1 - t) ** 3 * a[k] + 3 * (1 - t) ** 2 * t * b[k]
                                      + 3 * (1 - t) * t ** 2 * c[k] + t ** 3 * d[k]) for k in (0, 1)))
    return result


def render_scene(scene, destination):
    scale = 2
    image = Image.new("RGB", (960 * scale, 640 * scale), scene.get("background", "#fffaf0"))
    if scene.get("background2"):
        image = gradient(image.size, scene.get("background", "#fffaf0"), scene["background2"])
    draw = ImageDraw.Draw(image)
    labels = []
    def point(p):
        return (round(p[0] * .96 * scale), round(p[1] * .64 * scale))
    def box(p):
        return (*point(p[:2]), *point(p[2:]))
    for item in flatten_elements(scene["elements"]):
        kind = item["type"]
        fill, stroke = item.get("fill"), item.get("stroke", "#31465c")
        width = round(item.get("width", 3) * scale)
        points = [point(p) for p in item.get("points", [])]
        if kind == "bezier":
            points = bezier_points(points)
        closed = kind == "polygon" or (kind == "bezier" and item.get("closed", False))
        filled = bool(fill and (kind in {"ellipse", "rect", "rounded_rect"} or closed))
        if filled and (scene.get("style", "soft") == "soft" or item.get("fill2")):
            mask = Image.new("L", image.size, 0)
            mask_draw = ImageDraw.Draw(mask)
            if closed:
                mask_draw.polygon(points, fill=255)
            elif kind == "rounded_rect":
                mask_draw.rounded_rectangle(box(item["box"]), radius=18 * scale, fill=255)
            else:
                getattr(mask_draw, "rectangle" if kind == "rect" else kind)(box(item["box"]), fill=255)
            if scene.get("style", "soft") == "soft" and item.get("shadow", True):
                shadow = Image.new("L", image.size, 0)
                shadow.paste(mask, (0, 3 * scale))
                shadow = shadow.filter(ImageFilter.GaussianBlur(4 * scale)).point(lambda p: round(p * .12))
                image.paste("#435364", (0, 0), shadow)
            if item.get("fill2"):
                bounds = mask.getbbox()
                if bounds:
                    image.paste(gradient((bounds[2] - bounds[0], bounds[3] - bounds[1]), fill, item["fill2"]),
                                bounds[:2], mask.crop(bounds))
                fill = None
            draw = ImageDraw.Draw(image)
        if kind in {"ellipse", "rect", "rounded_rect"}:
            method = {"ellipse": draw.ellipse, "rect": draw.rectangle, "rounded_rect": draw.rounded_rectangle}[kind]
            options = {"radius": 18 * scale} if kind == "rounded_rect" else {}
            if fill is not None or stroke is not None:
                method(box(item["box"]), fill=fill, outline=stroke, width=width, **options)
        elif kind in {"polygon", "line", "bezier"}:
            if closed:
                if fill is not None:
                    draw.polygon(points, fill=fill)
                points = points + [points[0]]
            if stroke:
                draw.line(points, fill=stroke, width=width, joint="curve")
                if not closed:
                    radius = width / 2
                    for x, y in (points[0], points[-1]):
                        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=stroke)
        elif kind == "arc":
            draw.arc(box(item["box"]), item.get("start", 0), item.get("end", 360), fill=stroke, width=width)
        else:
            font_path = Path("C:/Windows/Fonts/msyh.ttc")
            font = ImageFont.truetype(str(font_path), round(item.get("size", 30) * .96 * scale)) if font_path.exists() else ImageFont.load_default()
            x, y = point(item["at"])
            bounds = draw.textbbox((x, y), item["text"], font=font, anchor="lt")
            x = min(x, max(0, image.width - (bounds[2] - bounds[0]) - 12 * scale))
            for previous in labels:
                bounds = draw.textbbox((x, y), item["text"], font=font, anchor="lt")
                if bounds[0] < previous[2] and bounds[2] > previous[0] and bounds[1] < previous[3] and bounds[3] > previous[1]:
                    y = previous[3] + 6 * scale
            labels.append(draw.textbbox((x, y), item["text"], font=font, anchor="lt"))
            draw.text((x, y), item["text"], fill=fill or stroke, font=font, anchor="lt")
    image = image.resize((960, 640), Image.Resampling.LANCZOS)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="PNG")


class Sketchbook:
    def __init__(self, store, root=None):
        self.store = store
        self.root = Path(root) if root is not None else ROOT
        self.lock = asyncio.Lock()

    def key(self, cfg, contact=None):
        return f"sketches:{cfg['bot_qq']}:{contact or 'public'}"

    def records(self, cfg, contact=None):
        return self.store.get(self.key(cfg, contact), [])

    def folder(self, cfg, contact=None):
        if contact is not None and (not isinstance(contact, str) or not contact.isdigit()):
            raise ValueError("Drawings belong to one private contact or public activity")
        return self.root / "data/drawings" / str(cfg["bot_qq"]) / (contact or "public")

    def allowed_file(self, cfg, path, contact=None):
        file = Path(path)
        scopes = [None] if contact is None else [contact, None]
        return file.is_file() and file.suffix == ".png" and any(file.resolve().parent == self.folder(cfg, scope).resolve() and any(r.get("file") == str(file) for r in self.records(cfg, scope)) for scope in scopes)

    def context(self, contact, cfg, public=False, private_only=False):
        rows = [] if private_only else self.records(cfg)
        if not public and isinstance(contact, str) and contact.isdigit():
            rows = rows + self.records(cfg, contact)
        rows = [r for r in rows if Path(r["file"]).is_file()]
        if not rows:
            return ""
        return "\n你实际画完并保存的简笔画（不表示已发送或发布）：" + json.dumps([{k: r[k] for k in ("id", "title", "caption", "created")} for r in rows[-3:]], ensure_ascii=False)

    async def create(self, engine, cfg, brief="", contact=None):
        cfg = dict(cfg)
        if not cfg.get("autonomous_drawing") or cfg.get("paused") or not cfg.get("live"):
            return None
        self.folder(cfg, contact)
        generation = engine.memory_epoch(contact) if contact else None
        async with self.lock:
            system = build_personality(cfg, contact == cfg["owner_qq"], public=contact is None) + SCENE_GUIDE
            public_history = recent_public(self.store, cfg) if contact is None else []
            if contact is None:
                system += "独立公开创作，只用角色喜好和给定的公开活动。不提具体联系人、主人称呼、QQ号或私人关系。"
                system += history_context(public_history)
            else:
                system += "这是给当前好友的想象简笔画。没有明确提供的外貌、衣着和物件只算创作设定，不是对对方现实特征的判断，caption要分清想象与事实。"
            payload = {"brief": brief or "随兴画一幅自己想画的画，可以只是舒服、放松、开心的画面；没有想画的也可以skip。画完不必分享，不为凑动态或找人聊天而画。", "recent_titles": [r["title"] for r in self.records(cfg, contact)[-6:]]}
            raw = await engine.generate_aux("drawing", system, [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}], contact=contact)
            scene = parse_scene(raw)
            fresh = engine.config_reader()
            if scene["action"] == "skip" or fresh["revision"] != cfg["revision"] or fresh.get("paused") or not fresh.get("live") or not fresh.get("autonomous_drawing"):
                return None
            composition = str(scene.get("composition", ""))[:160]
            if contact is None and not await is_novel(engine, cfg, dict(kind="drawing", title=scene["title"], text=scene["caption"], composition=composition), public_history):
                self.store.set(f"last_public_drawing_skip:{cfg['bot_qq']}", dict(at=time.time(), title=scene["title"], reason="repeated_idea"))
                return None
            if contact and not engine.allowed(fresh, contact):
                return None
            if contact and engine.memory_epoch(contact) != generation:
                return None
            ident = uuid.uuid4().hex
            file = self.folder(cfg, contact) / (ident + ".png")
            await asyncio.to_thread(render_scene, scene, file)
            fresh = engine.config_reader()
            if fresh["revision"] != cfg["revision"] or fresh.get("paused") or not fresh.get("live") or (contact and engine.memory_epoch(contact) != generation):
                return None
            row = dict(id=ident, file=str(file), title=scene["title"], caption=scene["caption"], composition=composition, created=time.time(), scope=contact or "public")
            atomic_json(file.with_suffix(".json"), scene)
            self.store.set(self.key(cfg, contact), self.records(cfg, contact) + [row])
            if scene.get("mood") in MOODS:
                self.store.set(f"self_mood:{cfg['bot_qq']}", {"mood": scene["mood"], "at": time.time()})
            return row

    async def run(self, engine, now=None):
        cfg = engine.config_reader()
        now = datetime.now(TZ) if now is None else now
        if not cfg.get("autonomous_drawing") or not cfg.get("live") or cfg.get("paused") or now.hour < 8 or self.lock.locked():
            return None
        key = f"drawing_attempt:{cfg['bot_qq']}:{now.strftime('%Y-%m-%d')}"
        if self.store.get(key):
            return None
        self.store.set(key, {"at": now.timestamp()})
        return await self.create(engine, cfg)

    def pending_public(self, cfg):
        return next((r for r in reversed(self.records(cfg)) if not r.get("publication") and not r.get("public_skip") and self.allowed_file(cfg, r["file"])), None)

    def skip_public(self, cfg, ident):
        rows = self.records(cfg)
        for row in rows:
            if row["id"] == ident:
                row["public_skip"] = dict(at=time.time(), reason="repeated_idea")
        self.store.set(self.key(cfg), rows)

    def publication(self, cfg, ident, action):
        rows = self.records(cfg)
        for row in rows:
            if row["id"] == ident:
                row["publication"] = action
        self.store.set(self.key(cfg), rows)
