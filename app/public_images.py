"""Find reusable public images; never source pictures from private chat."""

import html
import io
import json
import re
import urllib.parse
import urllib.request

from settings import ROOT, atomic_json


class PublicImages:
    def search(self, topic):
        from PIL import Image

        keywords = (
            "blue whale ocean"
            if any(x in topic for x in ["鲸", "海", "鱼"])
            else "sky clouds landscape"
        )
        params = dict(
            action="query",
            format="json",
            generator="search",
            gsrsearch=keywords + " filetype:bitmap",
            gsrnamespace=6,
            gsrlimit=8,
            prop="imageinfo",
            iiprop="url|extmetadata",
            iiurlwidth=1280,
        )
        request = urllib.request.Request(
            "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params),
            headers={"User-Agent": "QQCompanion/0.3 personal-local-project"},
        )
        with urllib.request.urlopen(request, timeout=12) as response:
            data = json.load(response)
        for page in data.get("query", {}).get("pages", {}).values():
            info = (page.get("imageinfo") or [{}])[0]
            meta = info.get("extmetadata", {})
            license_name = meta.get("LicenseShortName", {}).get("value", "")
            if license_name not in {"Public domain", "CC0", "CC0 1.0"}:
                continue
            url = info.get("thumburl") or info.get("url", "")
            if urllib.parse.urlsplit(url).hostname != "upload.wikimedia.org":
                continue
            with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "QQCompanion/0.3"}),
                timeout=15,
            ) as response:
                raw = response.read(6 * 1024 * 1024 + 1)
            if len(raw) > 6 * 1024 * 1024:
                continue
            image = Image.open(io.BytesIO(raw))
            if image.width * image.height > 24_000_000:
                continue
            image = image.convert("RGB")
            image.thumbnail((1280, 1280))
            import hashlib

            directory = ROOT / "assets/public_images"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / (hashlib.sha256(raw).hexdigest() + ".jpg")
            image.save(path, format="JPEG", quality=88)

            def clean(value):
                return html.unescape(re.sub("<[^>]+>", "", str(value)))[:150]

            record = dict(
                file=str(path),
                source=info.get("descriptionurl", ""),
                license=license_name,
                author=clean(meta.get("Artist", {}).get("value", "")),
                title=page.get("title", ""),
            )
            atomic_json(path.with_suffix(".json"), record)
            return record
        return None

    def whale_fallback(self, library):
        from PIL import Image

        candidates = [
            item
            for key, item in library.items.items()
            if key in {"dsh_deyi", "dsh_ganfan", "dsh_ok", "dsh_jiazai"}
        ]
        if not candidates:
            return None
        import random

        item = random.choice(candidates)
        path = ROOT / "assets/public_images" / (item["id"] + ".png")
        path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(item["file"]) as source:
            source.seek(0)
            source.convert("RGBA").save(path, format="PNG")
        return dict(
            file=str(path),
            source=item["source"],
            license=item.get("license", "upstream fan-emote terms"),
            author=item.get("author", "whale-girl collection"),
            title=item["tag"],
        )
