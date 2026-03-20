from __future__ import annotations

import json
import os
import shutil
import sqlite3
import textwrap
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import BEIJING_TZ, ROLE_LABELS, SessionRecord


def decode_text(content: list[dict]) -> str:
    parts: list[str] = []
    for item in content:
        text = item.get("text")
        if text:
            parts.append(text)
    return "".join(parts).strip()


def sanitize_inline(text: str) -> str:
    return " ".join(text.replace("\r", "\n").split())


def normalize_message_text(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    if stripped.startswith("# AGENTS.md instructions for "):
        return ""
    if stripped.startswith("<turn_aborted>"):
        return ""
    return stripped


def parse_iso_timestamp(value: str | None) -> int:
    if not value:
        return 0
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


def discover_sqlite_path(codex_home: Path, stem: str) -> Path | None:
    candidates = []
    for path in codex_home.glob(f"{stem}_*.sqlite"):
        suffix = path.stem.split("_")[-1]
        try:
            index = int(suffix)
        except ValueError:
            continue
        candidates.append((index, path))
    if not candidates:
        return None
    candidates.sort()
    return candidates[-1][1]


def iter_rollout_files(codex_home: Path) -> Iterable[tuple[Path, bool]]:
    for dirname, archived in (("sessions", False), ("archived_sessions", True)):
        root = codex_home / dirname
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.jsonl")):
            yield path, archived


def load_sessions_from_db(db_path: Path) -> dict[str, SessionRecord]:
    records: dict[str, SessionRecord] = {}
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                id,
                title,
                updated_at,
                created_at,
                rollout_path,
                cwd,
                model_provider,
                archived
            FROM threads
            ORDER BY updated_at DESC, id DESC
            """
        )
        for row in cur.fetchall():
            path = row["rollout_path"]
            if not path:
                continue
            record = SessionRecord(
                session_id=row["id"],
                title=(row["title"] or "").strip() or row["id"],
                updated_at=int(row["updated_at"] or 0),
                created_at=int(row["created_at"] or 0),
                rollout_path=Path(path),
                cwd=(row["cwd"] or "").strip(),
                model_provider=(row["model_provider"] or "").strip(),
                archived=bool(row["archived"]),
            )
            records[record.session_id] = record
    finally:
        conn.close()
    return records


def merge_sessions_with_files(
    codex_home: Path, records: dict[str, SessionRecord]
) -> dict[str, SessionRecord]:
    for path, archived in iter_rollout_files(codex_home):
        try:
            with path.open("r", encoding="utf-8") as handle:
                first_line = handle.readline().strip()
        except OSError:
            continue
        if not first_line:
            continue
        try:
            first_obj = json.loads(first_line)
        except json.JSONDecodeError:
            continue
        payload = first_obj.get("payload", {})
        session_id = payload.get("id")
        if not session_id:
            continue
        record = records.get(session_id)
        if record is None:
            record = SessionRecord(
                session_id=session_id,
                title=session_id,
                updated_at=parse_iso_timestamp(payload.get("timestamp")),
                created_at=parse_iso_timestamp(payload.get("timestamp")),
                rollout_path=path,
                cwd=payload.get("cwd", ""),
                model_provider=payload.get("model_provider", ""),
                archived=archived,
            )
            records[session_id] = record
        else:
            if not record.rollout_path.exists():
                record.rollout_path = path
            record.archived = record.archived or archived
    return records


def load_conversation(record: SessionRecord) -> list[tuple[str, str]]:
    conversation: list[tuple[str, str]] = []
    with record.rollout_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "response_item":
                continue
            payload = obj.get("payload", {})
            if payload.get("type") != "message":
                continue
            role = payload.get("role")
            if role not in ("user", "assistant"):
                continue
            text = normalize_message_text(decode_text(payload.get("content", [])))
            if not text:
                continue
            conversation.append((role, text.strip()))
    return conversation


def populate_last_message(record: SessionRecord) -> None:
    last_role = ""
    last_text = ""
    try:
        for role, text in load_conversation(record):
            last_role = role
            last_text = sanitize_inline(text)
    except OSError:
        last_text = "[无法读取会话文件]"
    record.last_role = last_role
    record.last_text = last_text or "[无可显示文本]"


def build_search_document(record: SessionRecord) -> str:
    parts = [
        record.session_id,
        record.title,
        record.cwd,
        record.model_provider,
        record.last_preview,
    ]
    try:
        conversation = load_conversation(record)
    except OSError:
        conversation = []
    for role, text in conversation:
        label = ROLE_LABELS.get(role, role)
        parts.append(f"{label} {text}")
    return "\n".join(part for part in parts if part)


def load_all_sessions(codex_home: Path) -> list[SessionRecord]:
    db_path = discover_sqlite_path(codex_home, "state")
    records: dict[str, SessionRecord] = {}
    if db_path and db_path.exists():
        records = load_sessions_from_db(db_path)
    records = merge_sessions_with_files(codex_home, records)
    sessions = list(records.values())
    for record in sessions:
        populate_last_message(record)
        if not record.title or record.title == record.session_id:
            record.title = record.last_text[:60] or record.session_id
    sessions.sort(key=lambda item: (item.updated_at, item.session_id), reverse=True)
    return sessions


def format_time(epoch_seconds: int) -> str:
    if epoch_seconds <= 0:
        return "-"
    return datetime.fromtimestamp(epoch_seconds, tz=BEIJING_TZ).strftime(
        "%Y-%m-%d %H:%M"
    )


def char_display_width(char: str) -> int:
    return 2 if unicodedata.east_asian_width(char) in ("F", "W") else 1


def text_display_width(text: str) -> int:
    return sum(char_display_width(char) for char in text)


def fit_cell(text: str, width: int) -> str:
    if width <= 0:
        return ""
    clean_text = sanitize_inline(text)
    current_width = text_display_width(clean_text)
    if current_width <= width:
        return clean_text + (" " * (width - current_width))

    ellipsis = "…"
    ellipsis_width = text_display_width(ellipsis)
    if width <= ellipsis_width:
        return ellipsis

    target_width = width - ellipsis_width
    parts: list[str] = []
    used_width = 0
    for char in clean_text:
        char_width = char_display_width(char)
        if used_width + char_width > target_width:
            break
        parts.append(char)
        used_width += char_width
    return "".join(parts) + ellipsis + (" " * (width - used_width - ellipsis_width))


def sanitize_screen_text(text: str) -> str:
    sanitized: list[str] = []
    for char in text.replace("\r", "\n").replace("\t", "    "):
        if char == "\n" or char.isprintable():
            sanitized.append(char)
    return "".join(sanitized)


def sanitize_table_text(text: str) -> str:
    return sanitize_inline(sanitize_screen_text(text))


def build_detail_lines(text: str, width: int) -> list[str]:
    wrap_width = max(1, width)
    lines: list[str] = []
    for raw_line in sanitize_screen_text(text).split("\n"):
        if not raw_line:
            lines.append("")
            continue
        wrapped = textwrap.wrap(
            raw_line,
            width=wrap_width,
            replace_whitespace=False,
            drop_whitespace=False,
        )
        lines.extend(wrapped or [""])
    return lines


def compute_table_widths(total_width: int, separator_width: int) -> tuple[int, int, int, int]:
    available = max(4, total_width - separator_width * 3)
    minimums = [16, 18, 8, 12]
    widths = minimums[:]
    minimum_total = sum(minimums)

    if available < minimum_total:
        weights = minimums
        widths = [max(1, available * weight // minimum_total) for weight in weights]
        used = sum(widths)
        index = len(widths) - 1
        while used < available:
            widths[index] += 1
            used += 1
            index = (index - 1) % len(widths)
        while used > available:
            if widths[index] > 1:
                widths[index] -= 1
                used -= 1
            index = (index - 1) % len(widths)
        return tuple(widths)

    extra = available - minimum_total
    id_extra = min(extra, 18)
    widths[1] += id_extra
    extra -= id_extra

    title_extra = min(extra, 12)
    widths[2] += title_extra
    extra -= title_extra

    widths[3] += extra
    return tuple(widths)


def render_table_row(
    time_text: str,
    session_id: str,
    title: str,
    last_text: str,
    separator: str,
    total_width: int,
) -> str:
    time_w, id_w, title_w, last_w = compute_table_widths(total_width, len(separator))
    row = separator.join(
        (
            fit_cell(sanitize_table_text(time_text), time_w),
            fit_cell(sanitize_table_text(session_id), id_w),
            fit_cell(sanitize_table_text(title), title_w),
            fit_cell(sanitize_table_text(last_text), last_w),
        )
    )
    return row[:total_width]


def build_table_cells(
    time_text: str,
    session_id: str,
    title: str,
    last_text: str,
    separator: str,
    total_width: int,
) -> list[str]:
    time_w, id_w, title_w, last_w = compute_table_widths(total_width, len(separator))
    return [
        fit_cell(sanitize_table_text(time_text), time_w),
        fit_cell(sanitize_table_text(session_id), id_w),
        fit_cell(sanitize_table_text(title), title_w),
        fit_cell(sanitize_table_text(last_text), last_w),
    ]


def render_plain_table(sessions: list[SessionRecord]) -> str:
    term_width = shutil.get_terminal_size((120, 30)).columns
    header = render_table_row(
        "北京时间",
        "Session ID",
        "标题",
        "最后一句话",
        "  ",
        term_width,
    )
    lines = [header, "-" * term_width]
    for session in sessions:
        lines.append(
            render_table_row(
                format_time(session.updated_at),
                session.session_id,
                session.title,
                session.last_preview,
                "  ",
                term_width,
            )
        )
    return "\n".join(lines)


def render_plain_conversation(record: SessionRecord) -> str:
    lines = [
        f"Session ID: {record.session_id}",
        f"标题: {record.title}",
        f"时间(北京时间): {format_time(record.updated_at)}",
        f"CWD: {record.cwd or '-'}",
        f"Provider: {record.model_provider or '-'}",
        "",
    ]
    try:
        conversation = load_conversation(record)
    except OSError as exc:
        lines.append(f"[读取失败] {exc}")
        return "\n".join(lines)
    for role, text in conversation:
        label = ROLE_LABELS.get(role, role)
        lines.append(f"[{label}]")
        lines.append(text)
        lines.append("")
    return "\n".join(lines).rstrip()
