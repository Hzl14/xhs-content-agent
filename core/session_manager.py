from __future__ import annotations

from datetime import datetime

from models.schemas import PipelineState
from models.sessions import SessionRecord


class SessionManager:
    def __init__(self) -> None:
        self._states: dict[str, PipelineState] = {}
        self._records: dict[str, SessionRecord] = {}
        self._active_generations: dict[tuple[str, str], dict] = {}
        self._active_tasks: dict[tuple[str, str], dict] = {}
        self._pending_tasks: dict[tuple[str, str], dict] = {}

    def save_state(self, state: PipelineState) -> None:
        self._states[state.run_id] = state
        if state.run_id not in self._records:
            self._records[state.run_id] = SessionRecord(
                run_id=state.run_id,
                session_id=state.session_id,
                task_id=state.task_id,
                status=state.stage,
            )
        record = self._records[state.run_id]
        record.session_id = state.session_id
        record.task_id = state.task_id
        record.status = state.stage
        record.updated_at = datetime.utcnow()

    def get_state(self, run_id: str) -> PipelineState | None:
        return self._states.get(run_id)

    def save_active_generation(self, user_id: str, session_id: str, payload: dict) -> None:
        self._active_generations[(user_id, session_id)] = payload

    def get_active_generation(self, user_id: str, session_id: str) -> dict | None:
        return self._active_generations.get((user_id, session_id))

    def clear_active_generation(self, user_id: str, session_id: str) -> None:
        self._active_generations.pop((user_id, session_id), None)

    def save_active_task(self, user_id: str, session_id: str, payload: dict) -> None:
        self._active_tasks[(user_id, session_id)] = payload

    def get_active_task(self, user_id: str, session_id: str) -> dict | None:
        return self._active_tasks.get((user_id, session_id))

    def clear_active_task(self, user_id: str, session_id: str) -> None:
        self._active_tasks.pop((user_id, session_id), None)

    def save_pending_task(self, user_id: str, session_id: str, payload: dict) -> None:
        self._pending_tasks[(user_id, session_id)] = payload

    def get_pending_task(self, user_id: str, session_id: str) -> dict | None:
        return self._pending_tasks.get((user_id, session_id))

    def clear_pending_task(self, user_id: str, session_id: str) -> None:
        self._pending_tasks.pop((user_id, session_id), None)
