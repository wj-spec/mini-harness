"""Unified diff parser/applicator for the apply_patch tool.

只接受普通文本 diff（修改 / 新建 / 删除）；不支持重命名、二进制、mode 变更。
应用时严格校验上下文，容忍 hunk 行号偏移，但拒绝歧义匹配。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MAX_PATCH_CHARS = 200_000
MAX_FILES = 20
MAX_HUNKS_PER_FILE = 200
HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
DIFF_HEADER = re.compile(r"^diff --git a/(.+?) b/(.+)$")
NO_NEWLINE = "\\ No newline at end of file"


@dataclass(frozen=True)
class Hunk:
    old_start: int
    old_length: int
    new_start: int
    new_length: int
    lines: tuple[str, ...]


@dataclass(frozen=True)
class FilePatch:
    old_path: str | None
    new_path: str | None
    hunks: tuple[Hunk, ...]
    new_file: bool = False
    deleted_file: bool = False

    @property
    def target(self) -> str:
        path = self.new_path or self.old_path
        if not path:
            raise ValueError("patch file has no target path")
        return path

    @property
    def stats(self) -> tuple[int, int]:
        additions = sum(1 for hunk in self.hunks for line in hunk.lines if line.startswith("+"))
        deletions = sum(1 for hunk in self.hunks for line in hunk.lines if line.startswith("-"))
        return additions, deletions


def _diff_path(raw: str) -> str | None:
    value = raw.split("\t", 1)[0].strip()
    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    if value == "/dev/null":
        return None
    if not value:
        raise ValueError("empty path in patch")
    for prefix in ("a/", "b/"):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    if not value:
        raise ValueError("empty path in patch")
    return value


def _validate_path(path: str | None) -> None:
    if path is None:
        return
    if path.startswith("/") or path.startswith("\\"):
        raise ValueError(f"absolute path not allowed: {path}")
    parts = path.split("/")
    if any(part == ".." for part in parts):
        raise ValueError(f"path escape not allowed: {path}")
    if ".git" in parts:
        raise ValueError(f"writing .git is not allowed: {path}")


def parse_patch(text: str) -> list[FilePatch]:
    if not text.strip():
        raise ValueError("patch is empty")
    if len(text) > MAX_PATCH_CHARS:
        raise ValueError(f"patch exceeds {MAX_PATCH_CHARS} chars")
    lines = text.splitlines()
    files: list[FilePatch] = []
    current: dict | None = None

    def finish() -> None:
        nonlocal current
        if current is None:
            return
        old_path = current.get("old_path")
        new_path = current.get("new_path")
        hunks = tuple(Hunk(h["old_start"], h["old_length"], h["new_start"], h["new_length"], tuple(h["lines"])) for h in current.get("hunks", []))
        if current.get("binary"):
            raise ValueError("binary patches are not supported")
        if current.get("mode_only"):
            raise ValueError("mode-only patches are not supported")
        if not old_path and not new_path:
            current = None
            return
        if old_path and new_path and old_path != new_path:
            raise ValueError(f"rename patches are not supported: {old_path} -> {new_path}")
        _validate_path(old_path)
        _validate_path(new_path)
        new_file = bool(current.get("new_file")) or old_path is None
        deleted_file = bool(current.get("deleted_file")) or new_path is None
        if not hunks and not (new_file or deleted_file):
            raise ValueError("patch file section has no hunks")
        files.append(FilePatch(old_path, new_path, hunks, new_file, deleted_file))
        current = None

    for raw in lines:
        if raw.startswith("diff --git "):
            finish()
            match = DIFF_HEADER.match(raw)
            if not match:
                raise ValueError(f"malformed diff header: {raw[:120]}")
            current = {"old_path": _diff_path(f"a/{match.group(1)}"), "new_path": _diff_path(f"b/{match.group(2)}"), "hunks": []}
            continue
        # hunk 内容优先于元数据/文件头解析：新增行本身可能以 +++ 开头。
        if current is not None and current.get("hunks") and raw[:1] in (" ", "+", "-", "\\"):
            current["hunks"][-1]["lines"].append(raw)
            continue
        if raw.startswith("similarity index "):
            continue
        if raw.startswith(("old mode ", "new mode ")):
            if current is not None and not current.get("hunks"):
                current["mode_only"] = True
            continue
        if raw.startswith("index "):
            continue
        if raw.startswith("new file mode "):
            if current is not None:
                current["new_file"] = True
            continue
        if raw.startswith("deleted file mode "):
            if current is not None:
                current["deleted_file"] = True
            continue
        if raw.startswith("rename from ") or raw.startswith("rename to "):
            raise ValueError("rename patches are not supported; use write_file + delete or edit files separately")
        if raw.startswith("Binary files ") or raw.startswith("GIT binary patch"):
            raise ValueError("binary patches are not supported")
        if raw.startswith("--- "):
            if current is not None and current.get("hunks"):
                finish()
            if current is None:
                current = {"old_path": None, "new_path": None, "hunks": []}
            current["old_path"] = _diff_path(raw[4:])
            continue
        if raw.startswith("+++ "):
            if current is None:
                raise ValueError("patch has +++ without file header")
            current["new_path"] = _diff_path(raw[4:])
            continue
        if raw.startswith("@@"):
            if current is None:
                raise ValueError("patch has hunk without file header")
            match = HUNK_HEADER.match(raw)
            if not match:
                raise ValueError(f"malformed hunk header: {raw[:120]}")
            current["hunks"].append({"old_start": int(match.group(1)), "old_length": int(match.group(2) or 1),
                                     "new_start": int(match.group(3)), "new_length": int(match.group(4) or 1), "lines": []})
            continue
        if current is None:
            if raw.strip():
                raise ValueError(f"unexpected patch line: {raw[:120]}")
            continue
        if not raw.strip():
            raise ValueError("empty context line in patch must be prefixed with a space")
        raise ValueError(f"unexpected patch line: {raw[:120]}")
    finish()

    for file in files:
        _check_hunk_counts(file)
    if not files:
        raise ValueError("patch contains no file changes")
    if len(files) > MAX_FILES:
        raise ValueError(f"patch touches too many files (>{MAX_FILES})")
    return files


def _check_hunk_counts(file: FilePatch) -> None:
    for hunk in file.hunks:
        old_count = sum(1 for line in hunk.lines if line.startswith((" ", "-")))
        new_count = sum(1 for line in hunk.lines if line.startswith((" ", "+")))
        if old_count != hunk.old_length or new_count != hunk.new_length:
            raise ValueError(
                f"hunk line count mismatch for {file.target}: "
                f"expected -{hunk.old_length}/+{hunk.new_length}, got -{old_count}/+{new_count}"
            )


def _expected_index(hunk: Hunk) -> int:
    if hunk.old_length == 0:
        return max(0, hunk.old_start)
    return max(0, hunk.old_start - 1)


def apply_hunks(current: str, file: FilePatch) -> tuple[str, int, int]:
    """把 hunk 应用到当前文本；返回 (new_text, additions, deletions)。"""
    lines = current.split("\n")
    result: list[str] = []
    cursor = 0
    additions = deletions = 0
    search_window = 200
    for hunk in file.hunks:
        old_block = [line[1:] for line in hunk.lines if line.startswith((" ", "-"))]
        expected = max(cursor, _expected_index(hunk))
        positions = list(range(expected, min(len(lines), expected + search_window) + 1))
        if expected > 0:
            positions += list(range(max(cursor, expected - search_window), expected))
        match = None
        for position in positions:
            if lines[position:position + len(old_block)] == old_block:
                match = position
                break
        if match is None:
            raise ValueError(f"hunk context mismatch near line {hunk.old_start} in {file.target}; re-read the file and regenerate the patch")
        if match < cursor:
            raise ValueError(f"overlapping hunks in {file.target}")
        result.extend(lines[cursor:match])
        position = match
        for line in hunk.lines:
            prefix = line[0]
            content = line[1:]
            if prefix == " ":
                result.append(lines[position])
                position += 1
            elif prefix == "-":
                position += 1
                deletions += 1
            elif prefix == "+":
                result.append(content)
                additions += 1
        cursor = position
    result.extend(lines[cursor:])
    return "\n".join(result), additions, deletions


def new_file_text(file: FilePatch) -> str:
    added = [line[1:] for hunk in file.hunks for line in hunk.lines if line.startswith("+")]
    return "\n".join(added) + ("\n" if added else "")


def render_summary(files: list[FilePatch]) -> str:
    total_add = sum(file.stats[0] for file in files)
    total_del = sum(file.stats[1] for file in files)
    lines = [f"apply_patch · {len(files)} file(s), +{total_add} -{total_del}"]
    for file in files:
        additions, deletions = file.stats
        action = "new" if file.new_file else "delete" if file.deleted_file else "modify"
        lines.append(f"  {action}: {file.target} (+{additions} -{deletions})")
    return "\n".join(lines)
