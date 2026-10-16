"""Pre-compressed SPA Static Asset Dispatcher.

Directly serves build-time `.br` (Brotli) and `.gz` (Gzip) pre-compressed files
with zero Python CPU compression overhead. Configures immutable caching for /assets/*
and Weak ETag 304 negotiation for index.html.
"""
from __future__ import annotations

import hashlib
import mimetypes
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse


def _compute_file_etag(path: Path) -> str:
    """Compute a lightweight Weak ETag using mtime and file size."""
    stat = path.stat()
    raw = f"{stat.st_mtime_ns}-{stat.st_size}"
    h = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f'W/"{h}"'


def mount_precompressed_spa(app: FastAPI, static_dir: str | Path) -> None:
    """Mount the SPA static file handler at root catch-all route."""
    root = Path(static_dir).resolve()

    PLACEHOLDER_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>LunaBot Autochat Dashboard</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
           background: #0f172a; color: #f8fafc; display: flex; align-items: center;
           justify-content: center; height: 100vh; margin: 0; }
    .box { text-align: center; max-width: 500px; padding: 32px; background: #1e293b;
           border-radius: 12px; border: 1px solid #334155; }
    h1 { font-size: 20px; color: #38bdf8; margin-bottom: 12px; }
    p { font-size: 14px; color: #94a3b8; line-height: 1.6; }
    code { background: #0f172a; padding: 3px 6px; border-radius: 4px; color: #a5b4fc; }
  </style>
</head>
<body>
  <div class="box">
    <h1>🌙 LunaBot Autochat Dashboard</h1>
    <p>前端静态资源尚未构建。<br>请在 <code>src/services/autochat_dashboard/frontend</code> 目录下执行：</p>
    <p><code>npm install && npm run build</code></p>
    <p style="margin-top: 16px; font-size: 12px; color: #64748b;">构建产物将自动输出至 <code>static/</code> 目录并开启 .br/.gz 极速分发。</p>
  </div>
</body>
</html>"""

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(request: Request, full_path: str):
        # 1. Never intercept API routes
        if full_path.startswith("api/") or full_path == "api":
            raise HTTPException(status_code=404, detail="API endpoint not found")

        # Fallback if static directory does not exist or empty
        index_file = root / "index.html"
        if not root.exists() or not index_file.exists():
            return HTMLResponse(content=PLACEHOLDER_HTML, status_code=200)

        # Resolve requested file path safely preventing path traversal
        clean_path = full_path.lstrip("/")
        target_file = (root / clean_path).resolve() if clean_path else index_file

        is_subpath = False
        try:
            target_file.relative_to(root)
            is_subpath = True
        except ValueError:
            pass

        if not is_subpath or not target_file.is_file():
            # SPA fallback: any non-file path routes back to index.html
            target_file = index_file

        is_index = (target_file == index_file)
        is_asset = clean_path.startswith("assets/")

        # Guess mime type
        mime_type, _ = mimetypes.guess_type(str(target_file))
        if not mime_type:
            mime_type = "text/html" if is_index else "application/octet-stream"

        # 2. Check for pre-compressed伴生文件 (.br / .gz)
        accept_enc = request.headers.get("Accept-Encoding", "").lower()
        br_file = target_file.with_name(target_file.name + ".br")
        gz_file = target_file.with_name(target_file.name + ".gz")

        selected_file = target_file
        content_encoding = None

        if "br" in accept_enc and br_file.is_file():
            selected_file = br_file
            content_encoding = "br"
        elif "gzip" in accept_enc and gz_file.is_file():
            selected_file = gz_file
            content_encoding = "gzip"

        headers = {}
        if content_encoding:
            headers["Content-Encoding"] = content_encoding
            headers["Vary"] = "Accept-Encoding"

        # 3. Cache-Control & ETag negotiation
        if is_asset:
            # Hashed assets in /assets/*: cache forever
            headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            # index.html & root routes: no-cache with Weak ETag negotiation
            etag = _compute_file_etag(target_file)
            headers["Cache-Control"] = "no-cache"
            headers["ETag"] = etag

            if_none_match = request.headers.get("If-None-Match")
            if if_none_match and if_none_match == etag:
                return Response(status_code=304, headers=headers)

        return FileResponse(
            path=str(selected_file),
            media_type=mime_type,
            headers=headers,
        )
