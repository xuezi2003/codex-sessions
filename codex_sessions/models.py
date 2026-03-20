from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path


ROLE_LABELS = {
    "user": "用户",
    "assistant": "助手",
}

BEIJING_TZ = timezone(timedelta(hours=8))
SEARCH_DB_NAME = "session_search.sqlite"
SEARCH_SCHEMA_VERSION = 1


@dataclass
class SessionRecord:
    session_id: str
    title: str
    updated_at: int
    created_at: int
    rollout_path: Path
    cwd: str
    model_provider: str
    archived: bool = False
    last_role: str = ""
    last_text: str = ""

    @property
    def last_preview(self) -> str:
        prefix = ""
        if self.last_role == "user":
            prefix = "U: "
        elif self.last_role == "assistant":
            prefix = "A: "
        return prefix + self.last_text


@dataclass
class DetailLine:
    text: str
    style: str = "body"
