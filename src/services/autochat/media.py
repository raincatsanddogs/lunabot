from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path
from collections import Counter

from .store import dump


class MediaStore:
    def __init__(self, store, settings, clock):
        self.store, self.settings, self.clock = store, settings, clock
        self.root = store.root / "media"
        self.root.mkdir(exist_ok=True)
        self.inflight = Counter()
        self.release_contexts = None

    def pin(self, asset_ids):
        self.inflight.update(asset_ids)

    def unpin(self, asset_ids):
        self.inflight.subtract(asset_ids)
        self.inflight += Counter()  # Drop zero counts; other jobs keep their leases.

    def path(self, asset_id):
        if len(asset_id) != 64 or any(c not in "0123456789abcdef" for c in asset_id):
            raise ValueError("Invalid asset ID")
        return self.root / asset_id

    def pinned(self):
        pins = set(self.inflight)
        for row in self.store.db.execute("SELECT value FROM kv WHERE key LIKE 'state:%'"):
            state = json.loads(row[0])
            for message in state.get("context", []):
                content = message.get("content")
                if isinstance(content, list):
                    pins.update(p["asset_id"] for p in content if p.get("type") == "image_ref")
        # Received messages must survive until their first processing opportunity.
        for row in self.store.db.execute('SELECT payload FROM events WHERE handled=0'):
            event = json.loads(row[0])
            pins.update(
                s['data']['asset_id']
                for s in event['segments']
                if s.get('type') == 'image' and s.get('data', {}).get('asset_id')
            )
        return pins

    def usage(self):
        return sum(p.stat().st_size for p in self.root.iterdir() if p.is_file())

    def clean(self, required=0):
        now, pins = self.clock.now(), self.pinned()
        used = self.usage()
        for row in self.store.db.execute("SELECT * FROM assets ORDER BY last_used,id").fetchall():
            if row["id"] in pins:
                continue
            age = now - row["created"]
            path = self.path(row["id"])
            if path.exists() and (
                age > self.settings.original_days * 86400
                or used + required > self.settings.media_bytes
            ):
                used -= path.stat().st_size
                path.unlink()
                with self.store.db:
                    self.store.db.execute("UPDATE assets SET available=0 WHERE id=?", (row["id"],))
            preview = path.with_suffix(".preview.webp")
            if preview.exists() and (
                age > self.settings.preview_days * 86400
                or used + required > self.settings.media_bytes
            ):
                used -= preview.stat().st_size
                preview.unlink()
        return used + required <= self.settings.media_bytes

    async def ingest(self, source):
        import aiohttp
        from PIL import Image

        if source.startswith("data:"):
            encoded = source.split(",", 1)[1]
            if len(encoded) > self.settings.media_file_bytes * 4 / 3 + 4:
                raise ValueError("Image too large")
            data = base64.b64decode(encoded, validate=True)
        elif source.startswith(("https://", "http://")):
            chunks, length = [], 0
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                async with session.get(source) as response:
                    if response.status != 200:
                        raise ValueError(f"Image download failed: {response.status}")
                    async for chunk in response.content.iter_chunked(65536):
                        length += len(chunk)
                        if length > self.settings.media_file_bytes:
                            raise ValueError("Image too large")
                        chunks.append(chunk)
            data = b"".join(chunks)
        else:
            raise ValueError("Only platform HTTP URLs or inline images are accepted")
        if len(data) > self.settings.media_file_bytes:
            raise ValueError("Image too large")
        with Image.open(io.BytesIO(data)) as picture:
            picture.verify()
        with Image.open(io.BytesIO(data)) as picture:
            mime = Image.MIME.get(picture.format, "image/png")
            picture.thumbnail((480, 480))
            thumb = io.BytesIO()
            picture.convert("RGB").save(thumb, format="WEBP", quality=65)
            preview = thumb.getvalue()
        asset_id = hashlib.sha256(data).hexdigest()
        path = self.path(asset_id)
        now = self.clock.now()
        if not path.exists():
            required = len(data) + (
                0 if path.with_suffix('.preview.webp').exists() else len(preview)
            )
            if not self.clean(required):
                if self.release_contexts:
                    self.release_contexts()
                if not self.clean(required):
                    raise ValueError("Media quota reached; active context assets are protected")
            path.write_bytes(data)
            path.with_suffix(".preview.webp").write_bytes(preview)
        with self.store.db:
            self.store.db.execute(
                "INSERT INTO assets VALUES (?,?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET last_used=excluded.last_used,available=1",
                (asset_id, mime, len(data), now, now),
            )
        return asset_id

    def materialize(self, messages):
        import copy

        result = copy.deepcopy(messages)
        for message in result:
            if not isinstance(message.get("content"), list):
                continue
            content = []
            for part in message["content"]:
                if part.get("type") != "image_ref":
                    content.append(part)
                    continue
                asset_id = part["asset_id"]
                row = self.store.db.execute(
                    "SELECT mime FROM assets WHERE id=?", (asset_id,)
                ).fetchone()
                path = self.path(asset_id)
                if not row or not path.exists():
                    content.append(
                        {"type": "text", "text": f"[附件 {asset_id} 已不可用；未查看原图]"}
                    )
                else:
                    content.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{row['mime']};base64,"
                                + base64.b64encode(path.read_bytes()).decode()
                            },
                        }
                    )
            message["content"] = content
        return result
