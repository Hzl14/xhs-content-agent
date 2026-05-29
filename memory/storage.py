from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import List

from memory.models import Message, Turn, SessionSummary, SessionState

try:
    import fcntl  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None


class FileStorage:
    """
    基于文件系统的持久化存储。
    修复原版并发写入竞态：user_index 写入使用文件锁（fcntl，Unix）；
    Windows 环境自动降级为无锁写入（单进程场景安全）。
    """

    def __init__(self, base_dir: str = "data/memory") -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    # ── 目录路径 ──────────────────────────────────────────────────────────────

    def _user_dir(self, user_id: str) -> Path:
        path = self.base_dir / user_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _session_dir(self, user_id: str, session_id: str) -> Path:
        path = self._user_dir(user_id) / session_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ── 原始消息（append-only，无需锁）──────────────────────────────────────

    def append_raw_message(self, user_id: str, session_id: str, message: Message) -> None:
        path = self._session_dir(user_id, session_id) / "messages.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(message), ensure_ascii=False) + "\n")

    def load_recent_raw_messages(
        self, user_id: str, session_id: str, max_messages: int = 40
    ) -> list[dict]:
        path = self._session_dir(user_id, session_id) / "messages.jsonl"
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()[-max_messages:]
        result = []
        for line in lines:
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return result

    # ── 归档 Turn（append-only）──────────────────────────────────────────────

    def append_archive_turns(
        self, user_id: str, session_id: str, turns: List[Turn]
    ) -> None:
        path = self._session_dir(user_id, session_id) / "archive_turns.jsonl"
        with path.open("a", encoding="utf-8") as f:
            for turn in turns:
                f.write(json.dumps(asdict(turn), ensure_ascii=False) + "\n")

    # ── 摘要（覆盖写，单文件无竞态）─────────────────────────────────────────

    def save_summary(
        self, user_id: str, session_id: str, summary: SessionSummary
    ) -> None:
        path = self._session_dir(user_id, session_id) / "summary.json"
        path.write_text(
            json.dumps(asdict(summary), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def load_summary(self, user_id: str, session_id: str) -> SessionSummary | None:
        path = self._session_dir(user_id, session_id) / "summary.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return SessionSummary(**data)
        except Exception:  # noqa: BLE001
            return None

    # ── SessionState ─────────────────────────────────────────────────────────

    def save_state(self, state: SessionState) -> None:
        payload = {
            "user_id": state.user_id,
            "session_id": state.session_id,
            "active_turns": [asdict(t) for t in state.active_turns],
            "pending_summary_turns": [asdict(t) for t in state.pending_summary_turns],
            "summary": asdict(state.summary) if state.summary else None,
            "total_active_tokens": state.total_active_tokens,
            "updated_at": state.updated_at,
        }
        path = self._session_dir(state.user_id, state.session_id) / "state.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def load_state(self, user_id: str, session_id: str) -> SessionState:
        path = self._session_dir(user_id, session_id) / "state.json"
        if not path.exists():
            return SessionState(
                user_id=user_id,
                session_id=session_id,
                summary=self.load_summary(user_id, session_id),
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return SessionState(user_id=user_id, session_id=session_id)

        summary = (
            SessionSummary(**data["summary"]) if data.get("summary") else
            self.load_summary(user_id, session_id)
        )
        return SessionState(
            user_id=data["user_id"],
            session_id=data["session_id"],
            active_turns=[self._dict_to_turn(t) for t in data.get("active_turns", [])],
            pending_summary_turns=[
                self._dict_to_turn(t) for t in data.get("pending_summary_turns", [])
            ],
            summary=summary,
            total_active_tokens=data.get("total_active_tokens", 0),
            updated_at=data.get("updated_at", time.time()),
        )

    # ── User Index（修复并发写入竞态）────────────────────────────────────────

    def load_user_sessions(self, user_id: str) -> list[dict]:
        path = self._user_dir(user_id) / "user_index.json"
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("sessions", [])
        except Exception:  # noqa: BLE001
            return []

    def update_user_index(
        self,
        user_id: str,
        session_id: str,
        summary_keywords: list[str],
        updated_at: float,
    ) -> None:
        path = self._user_dir(user_id) / "user_index.json"
        if fcntl is not None:
            try:
                self._locked_update_index(path, session_id, summary_keywords, updated_at)
                return
            except Exception:  # noqa: BLE001
                pass
        self._unlocked_update_index(path, session_id, summary_keywords, updated_at)

    def _locked_update_index(
        self, path: Path, session_id: str, keywords: list[str], updated_at: float
    ) -> None:
        """使用 fcntl 文件锁防止并发竞态（Unix/Linux/macOS）。"""
        if fcntl is None:
            raise RuntimeError("fcntl is unavailable on this platform")
        with path.open("a+", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                content = f.read()
                payload = json.loads(content) if content.strip() else {"sessions": []}
                self._merge_session_entry(payload, session_id, keywords, updated_at)
                f.seek(0)
                f.truncate()
                json.dump(payload, f, ensure_ascii=False, indent=2)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def _unlocked_update_index(
        self, path: Path, session_id: str, keywords: list[str], updated_at: float
    ) -> None:
        """无锁写入（Windows / 单进程场景）。"""
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                payload = {"sessions": []}
        else:
            payload = {"sessions": []}
        self._merge_session_entry(payload, session_id, keywords, updated_at)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _merge_session_entry(
        payload: dict, session_id: str, keywords: list[str], updated_at: float
    ) -> None:
        for item in payload.get("sessions", []):
            if item["session_id"] == session_id:
                item["summary_keywords"] = keywords
                item["updated_at"] = updated_at
                return
        payload.setdefault("sessions", []).append({
            "session_id": session_id,
            "summary_keywords": keywords,
            "updated_at": updated_at,
        })

    # ── 工具方法 ─────────────────────────────────────────────────────────────

    @staticmethod
    def _dict_to_turn(t: dict) -> Turn:
        user_msg = Message(**t["user_message"])
        assistant_msg = (
            Message(**t["assistant_message"]) if t.get("assistant_message") else None
        )
        return Turn(
            turn_id=t["turn_id"],
            user_message=user_msg,
            assistant_message=assistant_msg,
            timestamp=t.get("timestamp", 0.0),
            turn_summary=t.get("turn_summary", ""),
            turn_keywords=t.get("turn_keywords", []),
        )
