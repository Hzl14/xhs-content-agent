from core.session_manager import SessionManager
from models.schemas import PipelineState


class SessionService:
    def __init__(self, session_manager: SessionManager) -> None:
        self.session_manager = session_manager

    def save(self, state: PipelineState) -> None:
        self.session_manager.save_state(state)

    def get(self, run_id: str) -> PipelineState | None:
        return self.session_manager.get_state(run_id)

    def save_active_generation(self, user_id: str, session_id: str, payload: dict) -> None:
        self.session_manager.save_active_generation(user_id, session_id, payload)

    def get_active_generation(self, user_id: str, session_id: str) -> dict | None:
        return self.session_manager.get_active_generation(user_id, session_id)

    def clear_active_generation(self, user_id: str, session_id: str) -> None:
        self.session_manager.clear_active_generation(user_id, session_id)

    def save_active_task(self, user_id: str, session_id: str, payload: dict) -> None:
        self.session_manager.save_active_task(user_id, session_id, payload)

    def get_active_task(self, user_id: str, session_id: str) -> dict | None:
        return self.session_manager.get_active_task(user_id, session_id)

    def clear_active_task(self, user_id: str, session_id: str) -> None:
        self.session_manager.clear_active_task(user_id, session_id)

    def save_pending_task(self, user_id: str, session_id: str, payload: dict) -> None:
        self.session_manager.save_pending_task(user_id, session_id, payload)

    def get_pending_task(self, user_id: str, session_id: str) -> dict | None:
        return self.session_manager.get_pending_task(user_id, session_id)

    def clear_pending_task(self, user_id: str, session_id: str) -> None:
        self.session_manager.clear_pending_task(user_id, session_id)
