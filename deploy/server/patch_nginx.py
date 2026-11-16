#!/usr/bin/env python3
"""把受控 location 插入唯一的 ai.muyaju.net server 块，保留原文件属性和备份。"""
import argparse
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

MARKER = "# automation-workbench-managed:start"


def server_blocks(text: str) -> list[tuple[int, int]]:
    blocks = []
    pending_server = False
    stack: list[tuple[str, int]] = []
    token = []
    quote = None
    comment = False
    escaped = False
    for index, char in enumerate(text):
        if comment:
            if char == "\n":
                comment = False
            continue
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char == "#":
            comment = True
            continue
        if char in "'\"":
            quote = char
            continue
        if char.isalnum() or char in "_-":
            token.append(char)
            continue
        if token:
            pending_server = "".join(token) == "server"
            token.clear()
        if char == "{":
            stack.append(("server" if pending_server else "other", index))
            pending_server = False
        elif char == "}":
            if not stack:
                raise ValueError("Nginx配置花括号不平衡")
            kind, start = stack.pop()
            if kind == "server":
                blocks.append((start, index))
    if stack or quote:
        raise ValueError("Nginx配置结构不完整")
    return blocks


def patch(target: Path, snippet_path: Path, backup: Path) -> str:
    if target.is_symlink() or not target.is_file() or snippet_path.is_symlink():
        raise ValueError("只允许普通配置文件")
    text = target.read_text(encoding="utf-8")
    snippet = snippet_path.read_text(encoding="utf-8").strip()
    if MARKER in text:
        return "unchanged"
    if MARKER not in snippet or "server {" in snippet:
        raise ValueError("location片段格式无效")
    matches = []
    for start, end in server_blocks(text):
        body = text[start + 1:end]
        names = re.findall(r"\bserver_name\s+([^;]+);", body)
        tls_server = (re.search(r"\blisten\s+[^;]*\b443\b[^;]*;", body) is not None
                      or re.search(r"\bssl_certificate\s+", body) is not None)
        if tls_server and any("ai.muyaju.net" in name.split() for name in names):
            matches.append((start, end))
    if len(matches) != 1:
        raise ValueError("必须唯一匹配ai.muyaju.net server块")
    if backup.exists():
        raise ValueError("备份文件已存在，拒绝覆盖")
    metadata = target.stat()
    shutil.copy2(target, backup)
    insertion = "\n" + snippet + "\n"
    updated = text[:matches[0][1]] + insertion + text[matches[0][1]:]
    handle, temporary_name = tempfile.mkstemp(prefix=".automation-workbench-", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, stat.S_IMODE(metadata.st_mode))
        if hasattr(os, "chown"):
            os.chown(temporary, metadata.st_uid, metadata.st_gid)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return "patched"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--snippet", required=True, type=Path)
    parser.add_argument("--backup", required=True, type=Path)
    args = parser.parse_args()
    print(patch(args.file.resolve(), args.snippet.resolve(), args.backup.resolve()))


if __name__ == "__main__":
    main()
