"""有尺寸上限的帮助索引。只截图请求页，缓存包含排版参数和样式。"""

from dataclasses import asdict, dataclass
from hashlib import sha256
from html import escape
import json
import math
from pathlib import Path
from uuid import uuid4

from .docs import HelpDocument, HelpEntry


# 修改 HTML 模板或解析规则时更新，保证旧布局不被复用。
RENDER_VERSION = "2"
STYLE = """
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: #fff; }
body { font-family: 'Microsoft YaHei', 'Noto Sans CJK SC', 'Noto Sans SC', sans-serif;
       font-size: var(--font-size); line-height: 1.5; color: #233044; }
#help-sheet { width: var(--width); padding: 28px 32px; background: #fff; }
header { padding-bottom: 18px; border-bottom: 2px solid #5878bc; }
.eyebrow { font-size: 15px; color: #5878bc; letter-spacing: 1px; }
h1 { margin: 6px 0; font-size: 28px; line-height: 1.4; overflow-wrap: anywhere; }
.meta, .legend { color: #59677a; font-size: 16px; }
.legend { margin-top: 8px; }
.category { margin-top: 18px; padding: 7px 12px; background: #eef3fc;
            border-left: 4px solid #5878bc; font-weight: 600; overflow-wrap: anywhere; }
.entry { padding: 13px 0; border-bottom: 1px solid #e6eaf0; }
.command { color: #234c8b; font-size: calc(var(--font-size) + 2px);
           line-height: 1.5; font-weight: 600; overflow-wrap: anywhere; }
.summary { margin-top: 5px; color: #455368; line-height: 1.5;
           display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2;
           overflow: hidden; overflow-wrap: anywhere; max-height: calc(var(--font-size) * 3); }
footer { margin-top: 20px; padding-top: 14px; border-top: 2px solid #dbe3ef;
         color: #59677a; font-size: 16px; line-height: 1.8; overflow-wrap: anywhere; }
code { color: #234c8b; font-family: inherit; }
.navigation { display: flex; justify-content: space-between; gap: 16px; }
.navigation > span { flex: 1; }
.detail { line-height: 1.7; overflow-wrap: anywhere; }
.detail h2 { font-size: 25px; }
.detail h3 { font-size: 23px; }
.detail blockquote { margin: 12px 0; padding: 8px 16px; border-left: 4px solid #5878bc; background: #eef3fc; }
.detail pre { white-space: pre-wrap; }
.detail img { max-width: 100%; }
"""


@dataclass(frozen=True)
class IndexOptions:
    page_size: int = 12
    width: int = 800
    font_size: int = 20
    max_height: int = 2000

    @classmethod
    def from_config(cls, values: dict) -> "IndexOptions":
        defaults = cls()
        limits = {"page_size": (1, 50), "width": (400, 1600), "font_size": (14, 40), "max_height": (600, 4000)}
        options = {}
        for key, (low, high) in limits.items():
            value = values.get(key, getattr(defaults, key))
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"helper.index.{key} 必须是 {low} 到 {high} 之间的整数")
            options[key] = value
        return cls(**options)


class PageOutOfRange(ValueError):
    def __init__(self, total: int):
        self.total = total
        super().__init__(f"页码必须是 1 到 {total} 之间的整数")


@dataclass(frozen=True)
class IndexPage:
    number: int
    total: int
    entries: tuple[HelpEntry, ...]
    image_path: Path | None = None


def _html(body: str, options: IndexOptions) -> str:
    variables = f"--font-size:{options.font_size}px;--width:{options.width}px"
    return f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><style>{STYLE}</style><body style="{variables}"><main id="help-sheet">{body}</main></body></html>'


def _header(doc: HelpDocument, number: int, total: int) -> str:
    permissions = {marker for entry in doc.entries for marker in entry.permissions}
    labels = []
    if "🛠️" in permissions:
        labels.append("🛠️ 超级管理指令")
    if "🔧" in permissions:
        labels.append("🔧 含群管理操作，权限见详情")
    legend = f'<div class="legend">{" · ".join(labels)}</div>' if labels else ""
    return (f'<header><div class="eyebrow">LUNABOT · 指令索引</div><h1>{escape(doc.title)}</h1>'
            f'<div class="meta">第 {number} / {total} 页 · 共 {len(doc.entries)} 项</div>{legend}</header>')


def _footer(doc: HelpDocument, number: int, total: int, measuring: bool = False) -> str:
    name = escape(doc.name)
    query = escape(doc.entries[0].primary.removeprefix("/")) if doc.entries else "指令名"
    previous = f'上一页：<code>/help {name} {number if measuring else number - 1}</code>' if number > 1 or measuring else "已是首页"
    following = f'下一页：<code>/help {name} {number if measuring else number + 1}</code>' if number < total or measuring else "已是末页"
    return (f'<footer><div class="navigation"><span>{previous}</span><span>{following}</span></div>'
            f'<div>详情：<code>/help {name} {query}</code></div>'
            '<div>也可在指令后加 <code>help</code> 查看详细用法。</div></footer>')


def _entry_html(entry: HelpEntry) -> str:
    marker = " ".join(entry.permissions)
    command = escape(f"{marker} {entry.primary}".strip())
    return f'<article class="entry"><div class="command">{command}</div><div class="summary">{escape(entry.summary)}</div></article>'


def build_index_html(doc: HelpDocument, entries: tuple[HelpEntry, ...], number: int, total: int, options: IndexOptions, measuring: bool = False) -> str:
    rows = []
    previous_category = None
    for entry in entries:
        if entry.category and entry.category != previous_category:
            rows.append(f'<div class="category">{escape(entry.category)}</div>')
        rows.append(_entry_html(entry))
        previous_category = entry.category
    return _html(_header(doc, number, total) + "".join(rows) + _footer(doc, number, total, measuring), options)


def page_text(doc: HelpDocument, page: IndexPage) -> str:
    lines = [f"{doc.title} — 指令索引", f"第 {page.number}/{page.total} 页，共 {len(doc.entries)} 项"]
    previous_category = None
    for entry in page.entries:
        if entry.category and entry.category != previous_category:
            lines.append(f"\n【{entry.category}】")
        lines.append(f"{' '.join(entry.permissions)} {entry.primary} — {entry.summary}".strip())
        previous_category = entry.category
    if any(entry.permissions for entry in page.entries):
        lines.append("🛠️ 超级管理指令；🔧 含群管理操作，权限见详情")
    if page.number > 1:
        lines.append(f"上一页：/help {doc.name} {page.number - 1}")
    if page.number < page.total:
        lines.append(f"下一页：/help {doc.name} {page.number + 1}")
    query = page.entries[0].primary.removeprefix("/") if page.entries else "指令名"
    lines.append(f"详情：/help {doc.name} {query}，或在指令后加 help")
    return "\n".join(lines)


def paginate(doc: HelpDocument, options: IndexOptions, heights: list[float], category_heights: dict[str, float], overhead: float) -> list[int]:
    """返回各页条目数；同一条命令及其简介不会跨页。"""
    counts = []
    count, used, previous_category = 0, overhead, None
    for entry, height in zip(doc.entries, heights):
        category_height = category_heights.get(entry.category, 0) if entry.category and entry.category != previous_category else 0
        if count and (count >= options.page_size or used + height + category_height > options.max_height):
            counts.append(count)
            count, used, previous_category = 0, overhead, None
            category_height = category_heights.get(entry.category, 0)
        if used + height + category_height > options.max_height:
            raise ValueError("单条帮助超出页面高度，请增大 helper.index.max_height")
        count += 1
        used += height + category_height
        previous_category = entry.category
    if count:
        counts.append(count)
    return counts


def _atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class HelpRenderer:
    def __init__(self, cache_dir: str | Path, page_factory, log_error=None):
        self.cache_dir = Path(cache_dir)
        self.page_factory = page_factory
        self.log_error = log_error or (lambda message: None)

    def _directory(self, doc: HelpDocument, options: IndexOptions) -> Path:
        fingerprint = json.dumps([RENDER_VERSION, STYLE, doc.name, doc.digest, asdict(options)], ensure_ascii=False, sort_keys=True)
        return self.cache_dir / sha256(fingerprint.encode("utf-8")).hexdigest()

    @staticmethod
    def _select(doc: HelpDocument, counts: list[int], number: int, path: Path | None = None) -> IndexPage:
        if not 1 <= number <= len(counts):
            raise PageOutOfRange(len(counts))
        start = sum(counts[:number - 1])
        return IndexPage(number, len(counts), doc.entries[start:start + counts[number - 1]], path)

    async def get_index(self, doc: HelpDocument, number: int, options: IndexOptions) -> IndexPage:
        directory = self._directory(doc, options)
        layout_path = directory / "pages.json"
        image_path = directory / f"index-{number}.png"
        counts = None
        try:
            saved = json.loads(layout_path.read_text(encoding="utf-8"))
            if isinstance(saved, list) and saved and all(type(n) is int and 0 < n <= options.page_size for n in saved) and sum(saved) == len(doc.entries):
                counts = saved
        except (OSError, ValueError):
            pass
        if counts:
            self._select(doc, counts, number)
            if image_path.is_file():
                return self._select(doc, counts, number, image_path)
        try:
            async with self.page_factory() as page:
                await page.set_viewport_size({"width": options.width, "height": 1})
                if counts is None:
                    # 仅排版简短索引以测量高度，不加载完整文档，也不截图其他页。
                    await page.set_content(build_index_html(doc, doc.entries, len(doc.entries), len(doc.entries), options, measuring=True))
                    await page.evaluate("document.fonts.ready")
                    metrics = await page.evaluate("""() => {
                        const height = node => {
                            const style = getComputedStyle(node);
                            return node.getBoundingClientRect().height + parseFloat(style.marginTop) + parseFloat(style.marginBottom);
                        };
                        const categories = {};
                        document.querySelectorAll('.category').forEach(node => { categories[node.textContent] = height(node); });
                        const style = getComputedStyle(document.querySelector('#help-sheet'));
                        return {rows: [...document.querySelectorAll('.entry')].map(height), categories,
                            overhead: height(document.querySelector('header')) + height(document.querySelector('footer'))
                                + parseFloat(style.paddingTop) + parseFloat(style.paddingBottom) + 2};
                    }""")
                    counts = paginate(doc, options, metrics["rows"], metrics["categories"], metrics["overhead"])
                    _atomic_write(layout_path, json.dumps(counts).encode("utf-8"))
                result = self._select(doc, counts, number)
                await page.set_content(build_index_html(doc, result.entries, number, result.total, options))
                await page.evaluate("document.fonts.ready")
                bounds = await page.locator("#help-sheet").bounding_box()
                if math.ceil(bounds["height"]) > options.max_height:
                    raise ValueError("帮助索引超出页面高度限制")
                png = await page.locator("#help-sheet").screenshot(type="png", scale="css")
                _atomic_write(image_path, png)
                return self._select(doc, counts, number, image_path)
        except PageOutOfRange:
            raise
        except Exception:
            self.log_error(f"渲染 {doc.name} 指令索引失败，回退文字")
            if counts is None:
                counts = [min(options.page_size, len(doc.entries) - i) for i in range(0, len(doc.entries), options.page_size)]
            return self._select(doc, counts, number)

    async def get_detail(self, doc: HelpDocument, entry: HelpEntry, options: IndexOptions) -> Path:
        import mistune

        markdown = entry.detail_markdown(doc.name)
        digest = sha256(markdown.encode("utf-8")).hexdigest()
        path = self._directory(doc, options) / f"detail-{digest}.png"
        if path.is_file():
            return path
        body = mistune.create_markdown(escape=True)(markdown)
        async with self.page_factory() as page:
            await page.set_viewport_size({"width": options.width, "height": 1})
            await page.set_content(_html(f'<div class="detail">{body}</div>', options))
            await page.evaluate("document.fonts.ready")
            _atomic_write(path, await page.locator("#help-sheet").screenshot(type="png", scale="css"))
        return path
