"""Local views of the existing sketchbook records; never publishes a drawing."""

import base64
import io
import json
import math
import re
from pathlib import Path

from PIL import Image

from settings import ROOT


class ArtGallery:
    def __init__(self, db, bot, root=ROOT):
        self.db, self.bot, self.root = db, str(bot), Path(root)

    def key(self, scope):
        if scope != "public" and not re.fullmatch(r"[0-9]+", scope):
            raise ValueError("作品分类无效。")
        return f"sketches:{self.bot}:{scope}"

    def entries(self):
        result = []
        for key, value in self.db.execute("SELECT key,value FROM state WHERE key LIKE ?", (f"sketches:{self.bot}:%",)):
            scope = key.rsplit(":", 1)[1]
            if scope != "public" and not re.fullmatch(r"[0-9]+", scope):
                continue
            try:
                records = json.loads(value)
            except ValueError:
                continue
            if not isinstance(records, list):
                continue
            for row in records:
                if not isinstance(row, dict) or not re.fullmatch(r"[0-9a-f]{32}", str(row.get("id", ""))):
                    continue
                expected = (self.root / "data/drawings" / self.bot / scope / (row["id"] + ".png")).resolve()
                if Path(row.get("file", "")).resolve() != expected or not expected.is_file():
                    continue
                result.append(dict(row, scope=scope, file=str(expected), bytes=expected.stat().st_size))
        return sorted(result, key=lambda row: row.get("created", 0), reverse=True)

    def get(self, ident, scope):
        self.key(str(scope))
        return next((row for row in self.entries() if row["id"] == ident and row["scope"] == scope), None)

    @staticmethod
    def preview(row, thumbnail=False):
        if thumbnail:
            with Image.open(row["file"]) as image:
                image = image.convert("RGB")
                image.thumbnail((360, 240), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                image.save(output, "PNG")
                data = output.getvalue()
        else:
            data = Path(row["file"]).read_bytes()
        return "data:image/png;base64," + base64.b64encode(data).decode("ascii")

    def page(self, query="", scope="all", page=1):
        if scope not in {"all", "public", "private"}:
            raise ValueError("作品分类无效。")
        rows = self.entries()
        public = sum(row["scope"] == "public" for row in rows)
        total_bytes = sum(row["bytes"] for row in rows)
        selected = [row for row in rows if (scope == "all" or (row["scope"] == "public") == (scope == "public"))
                    and str(query).strip().casefold() in (row.get("title", "") + row.get("caption", "")).casefold()]
        pages = max(1, math.ceil(len(selected) / 24))
        page = max(1, min(pages, int(page)))
        drawings = []
        for row in selected[(page - 1) * 24:page * 24]:
            drawings.append(dict(id=row["id"], scope=row["scope"], title=row.get("title", "未命名作品"),
                                 caption=row.get("caption", ""), created=row.get("created", 0), bytes=row["bytes"],
                                 thumbnail=self.preview(row, thumbnail=True)))
        return dict(drawings=drawings, total=len(rows), filtered=len(selected), public_count=public,
                    private_count=len(rows) - public, bytes=total_bytes, page=page, pages=pages)

    def delete(self, ident, scope):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.get(ident, scope)
            if not row:
                self.db.execute("COMMIT")
                return False
            key = self.key(scope)
            value = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
            records = [r for r in json.loads(value[0]) if r.get("id") != ident]
            self.db.execute("UPDATE state SET value=? WHERE key=?", (json.dumps(records, ensure_ascii=False), key))
            file = Path(row["file"])
            file.unlink(missing_ok=True)
            file.with_suffix(".json").unlink(missing_ok=True)
            self.db.execute("COMMIT")
            return True
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
