"""文档相对链接完整性检查（防止文档与文件移动不同步）。"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = sorted((REPO_ROOT / "docs").rglob("*.md"))
EXTRA = [REPO_ROOT / "README.md", REPO_ROOT / "AGENTS.md", REPO_ROOT / "docs" / "STRUCTURE.md"]

# 历史 research 文档记录的是已退役 app/tui.py 的事实来源，保留原文不改。
KNOWN_MISSING = {
    ("docs/research/context-partition-observability-2026-10-21.md", "../../app/tui.py"),
    ("docs/research/about-you-context-control-2026-10-20.md", "../../app/tui.py"),
}

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)#]+)(?:#[^)]*)?\)")


def _docs():
    seen = set()
    for path in DOCS + EXTRA:
        if path not in seen:
            seen.add(path)
            yield path


def test_relative_doc_links_exist():
    missing: list[tuple[str, str]] = []
    for doc in _docs():
        text = doc.read_text(encoding="utf-8", errors="replace")
        for match in LINK_RE.finditer(text):
            link = match.group(1).strip()
            if not link or link.startswith(("http://", "https://", "mailto:", "#")):
                continue
            if not (doc.parent / link).resolve().exists():
                key = (doc.relative_to(REPO_ROOT).as_posix(), link)
                if key in KNOWN_MISSING:
                    continue
                missing.append(key)
    assert missing == [], f"broken relative doc links: {missing}"


def test_docs_have_no_placeholders():
    for path in DOCS:
        text = path.read_text(encoding="utf-8", errors="replace")
        assert "TODO(填写)" not in text and "{{待补" not in text, path


def test_every_research_doc_is_referenced_somewhere():
    referenced: set[Path] = set()
    for doc in _docs():
        text = doc.read_text(encoding="utf-8", errors="replace")
        for match in LINK_RE.finditer(text):
            link = match.group(1).strip()
            if not link or link.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target = (doc.parent / link).resolve()
            try:
                target.relative_to(REPO_ROOT)
            except ValueError:
                continue
            referenced.add(target)
    research = {path.resolve() for path in (REPO_ROOT / "docs" / "research").glob("*.md")}
    orphans = sorted(path.relative_to(REPO_ROOT).as_posix() for path in research - referenced)
    assert orphans == [], f"unreferenced research docs: {orphans}"


def test_every_spec_is_referenced_somewhere():
    referenced: set[Path] = set()
    for doc in _docs():
        text = doc.read_text(encoding="utf-8", errors="replace")
        for match in LINK_RE.finditer(text):
            link = match.group(1).strip()
            if not link or link.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target = (doc.parent / link).resolve()
            try:
                target.relative_to(REPO_ROOT)
            except ValueError:
                continue
            referenced.add(target)
    specs = {path.resolve() for path in (REPO_ROOT / "docs" / "specs").glob("*.md")}
    orphans = sorted(path.relative_to(REPO_ROOT).as_posix() for path in specs - referenced)
    assert orphans == [], f"unreferenced specs: {orphans}"


HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.M)


def github_slug(heading: str) -> str:
    """近似 GitHub 锚点：小写、去标点、空格转连字符，保留中文/字母/数字。"""
    text = heading.strip().lower()
    kept = []
    for char in text:
        if char.isalnum() or char in " -_":
            kept.append(char)
    return "".join(kept).replace(" ", "-")


ANCHOR_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+?\.md)#([^)]+)\)")


def test_doc_anchors_resolve_to_headings():
    problems: list[tuple[str, str, str]] = []
    for doc in _docs():
        text = doc.read_text(encoding="utf-8", errors="replace")
        for match in ANCHOR_LINK_RE.finditer(text):
            target = (doc.parent / match.group(1)).resolve()
            anchor = match.group(2)
            if not target.is_file():
                continue
            slugs = {github_slug(item) for item in HEADING_RE.findall(
                target.read_text(encoding="utf-8", errors="replace"))}
            if anchor not in slugs:
                problems.append((doc.relative_to(REPO_ROOT).as_posix(),
                                 match.group(1), anchor))
    assert problems == [], f"unresolved doc anchors: {problems}"
