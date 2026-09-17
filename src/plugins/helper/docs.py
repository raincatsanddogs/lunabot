"""帮助文档解析与查询；不依赖 NoneBot 或浏览器。"""

from dataclasses import dataclass
from hashlib import sha256
import html
import re


@dataclass(frozen=True)
class HelpEntry:
    title: str
    category: str
    commands: tuple[str, ...]
    summary: str
    permissions: tuple[str, ...]
    content: str
    preamble: str = ""
    operation: str = ""

    @property
    def primary(self) -> str:
        return self.commands[0] if self.commands else self.title

    def detail_markdown(self, service: str) -> str:
        context = f"## {self.category}\n\n{self.preamble}\n\n" if self.preamble else ""
        usage = f"图片操作需配合 `/img` 使用，指令格式：`/img {self.operation} 参数`（参数见下文）。\n\n" if self.operation else ""
        return context + usage + self.content + f"\n\n> 发送 `/help {service}` 返回指令索引。"


@dataclass(frozen=True)
class HelpDocument:
    name: str
    title: str
    digest: str
    entries: tuple[HelpEntry, ...]

    def find(self, query: str) -> list[HelpEntry]:
        target = normalize_query(query)
        matches = []
        for entry in self.entries:
            names = [entry.title, *entry.commands]
            if entry.operation:
                names.extend((entry.operation, re.sub(r"[（(][^()（）]+[)）]$", "", entry.title)))
            if target in {normalize_query(name) for name in names}:
                matches.append(entry)
        return matches


def normalize_query(query: str) -> str:
    return " ".join(query.strip().removeprefix("/").split()).casefold()


def plain_inline(text: str) -> str:
    text = re.sub(r"!?\[([^\]]+)\]\([^)]*\)", r"\1", text)
    return html.unescape(text.replace("`", "").replace("**", "").strip())


def _markers(text: str) -> tuple[str, ...]:
    return tuple(label for marker, label in (("🛠", "🛠️"), ("🔧", "🔧")) if marker in text)


def _structure(lines: list[str]) -> dict[int, tuple[int, str]]:
    """-1 表示围栏代码行，0 表示分隔线，1..6 表示标题。"""
    result = {}
    fence = ""
    fence_length = 0
    for i, line in enumerate(lines):
        match = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            result[i] = (-1, "")
            if match and match[1][0] == fence and len(match[1]) >= fence_length and not match[2].strip():
                fence = ""
            continue
        if match:
            result[i] = (-1, "")
            fence, fence_length = match[1][0], len(match[1])
            continue
        heading = re.match(r"^ {0,3}(#{1,6})\s+(.+?)(?:\s+#+)?\s*$", line)
        if heading:
            result[i] = (len(heading[1]), heading[2].strip())
        elif re.fullmatch(r"\s*(?:-{3,}|\*{3,}|_{3,})\s*", line):
            result[i] = (0, "")
    return result


def _trim_section(lines: list[str]) -> str:
    lines = list(lines)
    while lines and (not lines[-1].strip() or re.fullmatch(r"\s*(?:---+|\[回到帮助目录\].*)\s*", lines[-1])):
        lines.pop()
    return "\n".join(lines).strip()


def parse_help_document(name: str, text: str) -> HelpDocument:
    lines = text.splitlines()
    structure = _structure(lines)
    headings = [(i, level, title) for i, (level, title) in structure.items() if level > 0]
    title = next((plain_inline(title) for _, level, title in headings if level == 1), name)
    toc_permissions = {}
    for line in lines:
        match = re.match(r"\s*[-*]\s*(.*?)\[([^\]]+)\]\(#[^)]*\)", line)
        if match:
            toc_permissions[plain_inline(match[2])] = match[1]

    entries = []
    category = ""
    preamble = ""
    for pos, (start, level, heading) in enumerate(headings):
        if level == 1:
            category, preamble = "", ""
        elif level == 2:
            # 目录中的四级分类标题不是正文分类。
            category = "" if heading.endswith("目录") else plain_inline(heading)
            end = next((i for i, depth, _ in headings[pos + 1:] if depth <= 3), len(lines))
            preamble = _trim_section(lines[start + 1:end]) if category else ""
        elif level == 3:
            end = next((i for i, depth, _ in headings[pos + 1:] if depth <= 3), len(lines))
            body = [lines[i] for i in range(start + 1, end) if structure.get(i, (0, ""))[0] != -1]
            definition = next((line.strip() for line in body if line.strip()), "")
            commands = tuple(dict.fromkeys(re.findall(r"`([^`]+)`", definition)))
            summary = next((plain_inline(line.lstrip()[1:]) for line in body if line.lstrip().startswith(">") and line.lstrip()[1:].strip()), "")
            entry_title = plain_inline(heading)
            operation = ""
            if name == "imgtool" and category == "图片操作":
                match = re.search(r"[（(]([a-zA-Z][\w-]*)[)）]$", entry_title)
                if match:
                    operation = match[1]
                    commands = (f"/img {operation}",)
            permissions = _markers(" ".join((heading, definition, summary, toc_permissions.get(entry_title, ""))))
            entries.append(HelpEntry(
                title=entry_title,
                category=category,
                commands=commands,
                summary=summary or entry_title,
                permissions=permissions,
                content=_trim_section(lines[start:end]),
                preamble=preamble,
                operation=operation,
            ))
    return HelpDocument(name, title, sha256(text.encode("utf-8")).hexdigest(), tuple(entries))
