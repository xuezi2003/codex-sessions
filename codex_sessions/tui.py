from __future__ import annotations

import curses
import shutil

from .models import DetailLine, ROLE_LABELS, SessionRecord
from .search import SearchIndex
from .store import (
    build_detail_lines,
    build_search_document,
    build_table_cells,
    fit_cell,
    format_time,
    load_conversation,
    sanitize_inline,
    sanitize_screen_text,
    text_display_width,
)


TIME_PAIR_ID = 1
ID_PAIR_ID = 2
TITLE_PAIR_ID = 3
LAST_PAIR_ID = 4
META_PAIR_ID = 5
USER_PAIR_ID = 6
ASSISTANT_PAIR_ID = 7
ACCENT_PAIR_ID = 8
SEARCH_PAIR_ID = 9


def is_key(key: str | int, *values: str | int) -> bool:
    return key in values


class SessionBrowser:
    def __init__(
        self,
        sessions: list[SessionRecord],
        search_index: SearchIndex | None = None,
        initial_query: str = "",
    ) -> None:
        self.all_sessions = list(sessions)
        self.sessions = list(sessions)
        self.search_index = search_index
        self.search_query = ""
        self.search_input = ""
        self.search_error = ""
        self.selected_index = 0
        self.list_offset = 0
        self.detail_offset = 0
        self.mode = "list"
        self.detail_lines: list[DetailLine] = []
        self.detail_record: SessionRecord | None = None
        self.detail_width = 0
        self.resume_session_id: str | None = None
        if initial_query:
            self.apply_search(initial_query)

    def run(self, stdscr: curses.window) -> None:
        curses.curs_set(0)
        stdscr.keypad(True)
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(TIME_PAIR_ID, curses.COLOR_CYAN, -1)
            curses.init_pair(ID_PAIR_ID, curses.COLOR_BLUE, -1)
            curses.init_pair(TITLE_PAIR_ID, curses.COLOR_YELLOW, -1)
            curses.init_pair(LAST_PAIR_ID, curses.COLOR_GREEN, -1)
            curses.init_pair(META_PAIR_ID, curses.COLOR_MAGENTA, -1)
            curses.init_pair(USER_PAIR_ID, curses.COLOR_CYAN, -1)
            curses.init_pair(ASSISTANT_PAIR_ID, curses.COLOR_GREEN, -1)
            curses.init_pair(ACCENT_PAIR_ID, curses.COLOR_YELLOW, -1)
            curses.init_pair(SEARCH_PAIR_ID, curses.COLOR_WHITE, curses.COLOR_BLUE)

        while True:
            stdscr.erase()
            height, width = stdscr.getmaxyx()
            if self.mode in ("list", "search"):
                self.draw_list(stdscr, height, width)
            else:
                self.draw_detail(stdscr, height, width)
            stdscr.refresh()
            try:
                key = stdscr.get_wch()
            except curses.error:
                continue
            action = self.handle_key(key, height)
            if action == "quit":
                return
            if action == "resume" and self.sessions:
                self.resume_session_id = self.current_session.session_id
                return

    def handle_key(self, key: str | int, height: int) -> str | None:
        if self.mode == "detail":
            return self.handle_detail_key(key, height)
        if self.mode == "search":
            return self.handle_search_key(key)
        return self.handle_list_key(key, height)

    def handle_list_key(self, key: str | int, height: int) -> str | None:
        visible_rows = max(1, height - 4)
        if is_key(key, "q", "\x1b"):
            return "quit"
        if is_key(key, "e", "E") and self.sessions:
            return "resume"
        if is_key(key, "/"):
            self.mode = "search"
            self.search_input = self.search_query
            return None
        if is_key(key, "c", "C"):
            self.search_input = ""
            self.apply_search("")
            return None
        if is_key(key, curses.KEY_UP, "k"):
            self.selected_index = max(0, self.selected_index - 1)
        elif is_key(key, curses.KEY_DOWN, "j"):
            self.selected_index = min(max(0, len(self.sessions) - 1), self.selected_index + 1)
        elif key == curses.KEY_NPAGE:
            self.selected_index = min(
                max(0, len(self.sessions) - 1), self.selected_index + visible_rows
            )
        elif key == curses.KEY_PPAGE:
            self.selected_index = max(0, self.selected_index - visible_rows)
        elif is_key(key, "g"):
            self.selected_index = 0
        elif is_key(key, "G"):
            self.selected_index = max(0, len(self.sessions) - 1)
        elif is_key(key, "\n", "\r", curses.KEY_ENTER) and self.sessions:
            self.open_detail()
        self.keep_selection_visible(visible_rows)
        return None

    def handle_search_key(self, key: str | int) -> str | None:
        if is_key(key, "\x1b"):
            self.mode = "list"
            self.search_input = self.search_query
            return None
        if is_key(key, "\n", "\r", curses.KEY_ENTER):
            self.mode = "list"
            self.search_query = sanitize_inline(self.search_input).strip()
            return None
        if key == curses.KEY_BACKSPACE or is_key(key, "\b", "\x7f"):
            self.search_input = self.search_input[:-1]
            self.apply_search(self.search_input)
            return None
        if is_key(key, "\x15"):
            self.search_input = ""
            self.apply_search("")
            return None
        if isinstance(key, str) and key.isprintable():
            self.search_input += key
            self.apply_search(self.search_input)
        return None

    def handle_detail_key(self, key: str | int, height: int) -> str | None:
        visible_rows = max(1, height - 2)
        max_offset = max(0, len(self.detail_lines) - visible_rows)
        if is_key(key, "q", "b", "\x1b"):
            self.mode = "list"
            self.detail_offset = 0
            return None
        if is_key(key, "e", "E") and self.sessions:
            return "resume"
        if is_key(key, curses.KEY_UP, "k"):
            self.detail_offset = max(0, self.detail_offset - 1)
        elif is_key(key, curses.KEY_DOWN, "j"):
            self.detail_offset = min(max_offset, self.detail_offset + 1)
        elif key == curses.KEY_NPAGE:
            self.detail_offset = min(max_offset, self.detail_offset + visible_rows)
        elif key == curses.KEY_PPAGE:
            self.detail_offset = max(0, self.detail_offset - visible_rows)
        elif is_key(key, "g"):
            self.detail_offset = 0
        elif is_key(key, "G"):
            self.detail_offset = max_offset
        return None

    def keep_selection_visible(self, visible_rows: int) -> None:
        if self.selected_index < self.list_offset:
            self.list_offset = self.selected_index
        elif self.selected_index >= self.list_offset + visible_rows:
            self.list_offset = self.selected_index - visible_rows + 1

    @property
    def current_session(self) -> SessionRecord:
        return self.sessions[self.selected_index]

    def apply_search(self, query: str) -> None:
        normalized = sanitize_inline(query).strip()
        self.search_query = normalized
        self.search_error = ""
        if not normalized:
            self.sessions = list(self.all_sessions)
            self.selected_index = 0
            self.list_offset = 0
            return

        try:
            if self.search_index is not None:
                id_map = {record.session_id: record for record in self.all_sessions}
                ordered_ids = self.search_index.search(normalized)
                self.sessions = [id_map[session_id] for session_id in ordered_ids if session_id in id_map]
            else:
                folded_query = normalized.casefold()
                self.sessions = [
                    record
                    for record in self.all_sessions
                    if folded_query in build_search_document(record).casefold()
                ]
                self.sessions.sort(
                    key=lambda item: (item.updated_at, item.session_id),
                    reverse=True,
                )
        except Exception as exc:
            self.sessions = []
            self.search_error = f"搜索失败: {exc}"

        self.selected_index = 0
        self.list_offset = 0

    def open_detail(self) -> None:
        if not self.sessions:
            return
        record = self.sessions[self.selected_index]
        self.detail_record = record
        self.detail_lines = self.build_detail_lines(record)
        self.detail_width = shutil.get_terminal_size((120, 30)).columns
        self.detail_offset = 0
        self.mode = "detail"

    def build_detail_lines(self, record: SessionRecord) -> list[DetailLine]:
        width = max(20, shutil.get_terminal_size((120, 30)).columns - 4)
        lines = [
            DetailLine(record.title or record.session_id, "title"),
            DetailLine("=" * 80, "divider"),
            DetailLine(
                f"Session ID  {record.session_id}    时间  {format_time(record.updated_at)}",
                "meta",
            ),
            DetailLine(
                f"Provider    {record.model_provider or '-'}    状态  {'已归档' if record.archived else '活跃'}",
                "meta",
            ),
            DetailLine(f"CWD         {record.cwd or '-'}", "path"),
            DetailLine("=" * 80, "divider"),
        ]
        try:
            conversation = load_conversation(record)
        except OSError as exc:
            return lines + [DetailLine(f"读取失败: {exc}", "assistant")]

        if not conversation:
            return lines + [DetailLine("[没有可显示的 user/assistant 对话]", "meta")]

        for index, (role, text) in enumerate(conversation, start=1):
            style = "user" if role == "user" else "assistant"
            label = ROLE_LABELS.get(role, role)
            lines.append(DetailLine(f"[{label} #{index:02d}]", style))
            for body_line in build_detail_lines(text, max(1, width)):
                lines.append(DetailLine(f"  {body_line}", "body"))
            lines.append(DetailLine("", "body"))
        return lines

    def draw_list(self, stdscr: curses.window, height: int, width: int) -> None:
        total_width = max(1, width - 1)
        self.safe_addnstr(
            0,
            0,
            "Codex Sessions  / 搜索  c 清空  ↑↓/j k 选择  Enter 查看  e 恢复  q 退出",
            total_width,
            stdscr,
            curses.A_BOLD | self.get_style_attr("accent"),
        )

        search_label = "搜索> " if self.mode == "search" else "搜索: "
        query = self.search_input if self.mode == "search" else (self.search_query or "全部")
        status = f"{search_label}{query}"
        count_text = f"结果 {len(self.sessions)}/{len(self.all_sessions)}"
        if self.search_error:
            status = self.search_error
        self.safe_addnstr(
            1,
            0,
            fit_cell(status, max(1, total_width - text_display_width(count_text) - 1)),
            total_width,
            stdscr,
            self.get_style_attr("search"),
        )
        if text_display_width(count_text) < total_width:
            self.safe_addnstr(
                1,
                max(0, total_width - text_display_width(count_text)),
                count_text,
                text_display_width(count_text),
                stdscr,
                curses.A_BOLD | self.get_style_attr("meta"),
            )

        self.draw_table_line(
            stdscr,
            2,
            "北京时间",
            "Session ID",
            "标题",
            "最后一句话",
            selected=False,
            header=True,
            total_width=total_width,
        )

        visible_rows = max(1, height - 4)
        self.keep_selection_visible(visible_rows)
        for row_index in range(visible_rows):
            session_index = self.list_offset + row_index
            if session_index >= len(self.sessions):
                break
            session = self.sessions[session_index]
            self.draw_table_line(
                stdscr,
                3 + row_index,
                format_time(session.updated_at),
                session.session_id,
                session.title,
                session.last_preview,
                selected=session_index == self.selected_index,
                header=False,
                total_width=total_width,
            )

        if not self.sessions:
            self.safe_addnstr(
                3,
                0,
                "[没有匹配到任何 session]",
                total_width,
                stdscr,
                self.get_style_attr("meta"),
            )

        footer = "列表默认按时间倒序排列"
        self.safe_addnstr(height - 1, 0, footer, total_width, stdscr, curses.A_DIM)

    def draw_table_line(
        self,
        stdscr: curses.window,
        y: int,
        time_text: str,
        session_id: str,
        title: str,
        last_text: str,
        *,
        selected: bool,
        header: bool,
        total_width: int,
    ) -> None:
        separator = " "
        cells = build_table_cells(
            time_text,
            session_id,
            title,
            last_text,
            separator,
            total_width,
        )
        if curses.has_colors():
            pair_attrs = [
                curses.color_pair(TIME_PAIR_ID),
                curses.color_pair(ID_PAIR_ID),
                curses.color_pair(TITLE_PAIR_ID),
                curses.color_pair(LAST_PAIR_ID),
            ]
        else:
            pair_attrs = [curses.A_NORMAL] * 4

        base_attr = curses.A_BOLD if header else curses.A_NORMAL
        if header:
            base_attr |= curses.A_UNDERLINE
        if selected:
            base_attr |= curses.A_REVERSE | curses.A_BOLD

        x = 0
        for index, cell in enumerate(cells):
            attr = base_attr | pair_attrs[index]
            self.safe_addnstr(y, x, cell, total_width - x, stdscr, attr)
            x += text_display_width(cell)
            if index < len(cells) - 1 and x < total_width:
                self.safe_addnstr(y, x, separator, total_width - x, stdscr, base_attr)
                x += len(separator)

    def draw_detail(self, stdscr: curses.window, height: int, width: int) -> None:
        total_width = max(1, width - 1)
        if self.detail_record is not None and self.detail_width != width:
            self.detail_lines = self.build_detail_lines(self.detail_record)
            self.detail_width = width

        header = "Session Detail  ↑↓/j k 滚动  e 恢复  q 返回"
        self.safe_addnstr(
            0,
            0,
            header,
            total_width,
            stdscr,
            curses.A_BOLD | self.get_style_attr("accent"),
        )

        visible_rows = max(1, height - 2)
        max_offset = max(0, len(self.detail_lines) - visible_rows)
        self.detail_offset = min(self.detail_offset, max_offset)
        for row_index in range(visible_rows):
            line_index = self.detail_offset + row_index
            if line_index >= len(self.detail_lines):
                break
            detail = self.detail_lines[line_index]
            self.safe_addnstr(
                1 + row_index,
                0,
                detail.text,
                total_width,
                stdscr,
                self.get_style_attr(detail.style),
            )

        progress = f"{self.detail_offset + 1}/{max(1, len(self.detail_lines))}"
        self.safe_addnstr(
            height - 1,
            0,
            progress,
            total_width,
            stdscr,
            curses.A_DIM | self.get_style_attr("meta"),
        )

    def get_style_attr(self, style: str) -> int:
        if not curses.has_colors():
            return curses.A_BOLD if style in {"title", "user", "assistant", "accent"} else curses.A_NORMAL

        if style == "title":
            return curses.A_BOLD | curses.color_pair(TITLE_PAIR_ID)
        if style == "meta":
            return curses.color_pair(META_PAIR_ID)
        if style == "path":
            return curses.A_DIM | curses.color_pair(META_PAIR_ID)
        if style == "divider":
            return curses.A_DIM | curses.color_pair(ACCENT_PAIR_ID)
        if style == "user":
            return curses.A_BOLD | curses.color_pair(USER_PAIR_ID)
        if style == "assistant":
            return curses.A_BOLD | curses.color_pair(ASSISTANT_PAIR_ID)
        if style == "accent":
            return curses.color_pair(ACCENT_PAIR_ID)
        if style == "search":
            return curses.A_BOLD | curses.color_pair(SEARCH_PAIR_ID)
        return curses.A_NORMAL

    def safe_addnstr(
        self,
        y: int,
        x: int,
        text: str,
        max_width: int,
        stdscr: curses.window,
        attr: int = curses.A_NORMAL,
    ) -> None:
        if max_width <= 0:
            return
        clipped = fit_cell(sanitize_screen_text(text).split("\n", 1)[0], max_width)
        try:
            stdscr.addnstr(y, x, clipped, max_width, attr)
        except curses.error:
            pass
