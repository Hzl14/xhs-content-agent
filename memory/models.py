from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List, Dict
import time


@dataclass
class Message:
    message_id: str
    role: str
    content: str
    timestamp: float
    token_count: int = 0
    meta: Dict = field(default_factory=dict)


@dataclass
class Turn:
    turn_id: str
    user_message: Message
    assistant_message: Optional[Message] = None
    timestamp: float = field(default_factory=time.time)
    turn_summary: str = ""
    turn_keywords: List[str] = field(default_factory=list)

    def retrieval_text(self) -> str:
        """用于检索的文本，摘要 + 关键词拼合。"""
        kw = ", ".join(w for w in self.turn_keywords if w)
        return f"{self.turn_summary}\n关键词: {kw}" if kw else self.turn_summary


@dataclass
class SessionSummary:
    session_id: str
    summary_version: int
    previous_summary: str
    current_summary: str
    keywords: List[str]
    covered_turn_ids: List[str]
    updated_at: float


@dataclass
class SessionState:
    user_id: str
    session_id: str
    active_turns: List[Turn] = field(default_factory=list)
    pending_summary_turns: List[Turn] = field(default_factory=list)
    summary: Optional[SessionSummary] = None
    total_active_tokens: int = 0
    updated_at: float = field(default_factory=time.time)

    @property
    def active_turn_count(self) -> int:
        return len(self.active_turns)

    @property
    def pending_turn_count(self) -> int:
        return len(self.pending_summary_turns)

    @property
    def effective_turn_count(self) -> int:
        return self.active_turn_count + self.pending_turn_count
