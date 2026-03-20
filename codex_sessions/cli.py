from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .search import SearchIndex
from .store import build_search_document, load_all_sessions, render_plain_conversation, render_plain_table, sanitize_inline
from .tui import SessionBrowser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-sessions",
        description="查看本地 Codex 的全部 session 记录，不受 resume picker 过滤影响。",
    )
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")),
        help="Codex 数据目录，默认读取 ~/.codex",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="直接以纯文本表格输出全部 session",
    )
    parser.add_argument(
        "--show",
        metavar="SESSION_ID",
        help="直接以纯文本输出某个 session 的精简对话",
    )
    parser.add_argument(
        "--search",
        metavar="QUERY",
        help="按标题、元数据和完整聊天记录搜索 session",
    )
    return parser


def filter_sessions(sessions, query: str, search_index: SearchIndex | None):
    normalized = sanitize_inline(query).strip()
    if not normalized:
        return list(sessions)
    if search_index is not None:
        id_map = {record.session_id: record for record in sessions}
        ordered_ids = search_index.search(normalized)
        return [id_map[session_id] for session_id in ordered_ids if session_id in id_map]
    folded_query = normalized.casefold()
    filtered = [
        record for record in sessions if folded_query in build_search_document(record).casefold()
    ]
    filtered.sort(key=lambda item: (item.updated_at, item.session_id), reverse=True)
    return filtered


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    codex_home = args.codex_home.expanduser()
    if not codex_home.exists():
        print(f"Codex 目录不存在: {codex_home}", file=sys.stderr)
        return 1

    sessions = load_all_sessions(codex_home)
    if not sessions:
        print("没有找到任何 session。", file=sys.stderr)
        return 1

    search_index: SearchIndex | None = None
    try:
        search_index = SearchIndex(codex_home)
        search_index.sync_sessions(sessions)
    except Exception:
        search_index = None
    try:
        if args.show:
            session = next((item for item in sessions if item.session_id == args.show), None)
            if session is None:
                print(f"未找到 session: {args.show}", file=sys.stderr)
                return 1
            print(render_plain_conversation(session))
            return 0

        filtered_sessions = sessions
        if args.search:
            filtered_sessions = filter_sessions(sessions, args.search, search_index)

        if args.list or not sys.stdout.isatty() or os.environ.get("TERM") == "dumb":
            print(render_plain_table(filtered_sessions))
            return 0

        browser = SessionBrowser(
            sessions,
            search_index=search_index,
            initial_query=args.search or "",
        )
        import curses

        curses.wrapper(browser.run)
        if browser.resume_session_id:
            try:
                os.execvp("codex", ["codex", "resume", browser.resume_session_id])
            except OSError as exc:
                print(f"执行 codex resume 失败: {exc}", file=sys.stderr)
                return 1
        return 0
    finally:
        if search_index is not None:
            search_index.close()
