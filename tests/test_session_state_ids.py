from core.session_manager import SessionManager
from models.schemas import PipelineState


def test_active_generation_is_scoped_by_user_and_session():
    manager = SessionManager()

    manager.save_active_generation("user-1", "session-a", {"task_id": "task-a"})
    manager.save_active_generation("user-1", "session-b", {"task_id": "task-b"})

    assert manager.get_active_generation("user-1", "session-a") == {"task_id": "task-a"}
    assert manager.get_active_generation("user-1", "session-b") == {"task_id": "task-b"}

    manager.clear_active_generation("user-1", "session-a")

    assert manager.get_active_generation("user-1", "session-a") is None
    assert manager.get_active_generation("user-1", "session-b") == {"task_id": "task-b"}


def test_active_task_is_scoped_by_user_and_session():
    manager = SessionManager()

    manager.save_active_task("user-1", "session-a", {"task_id": "task-a", "task_type": "source_collection"})
    manager.save_active_task("user-1", "session-b", {"task_id": "task-b", "task_type": "copywriting"})

    assert manager.get_active_task("user-1", "session-a") == {
        "task_id": "task-a",
        "task_type": "source_collection",
    }
    assert manager.get_active_task("user-1", "session-b") == {"task_id": "task-b", "task_type": "copywriting"}

    manager.clear_active_task("user-1", "session-a")

    assert manager.get_active_task("user-1", "session-a") is None
    assert manager.get_active_task("user-1", "session-b") == {"task_id": "task-b", "task_type": "copywriting"}


def test_session_record_tracks_session_and_task_ids():
    manager = SessionManager()
    state = PipelineState(
        run_id="run-1",
        session_id="session-1",
        task_id="task-1",
    )

    manager.save_state(state)

    record = manager._records["run-1"]
    assert record.run_id == "run-1"
    assert record.session_id == "session-1"
    assert record.task_id == "task-1"


def test_pending_task_is_scoped_by_user_and_session():
    manager = SessionManager()

    manager.save_pending_task("user-1", "session-a", {"task_id": "task-a"})
    manager.save_pending_task("user-1", "session-b", {"task_id": "task-b"})

    assert manager.get_pending_task("user-1", "session-a") == {"task_id": "task-a"}
    assert manager.get_pending_task("user-1", "session-b") == {"task_id": "task-b"}

    manager.clear_pending_task("user-1", "session-a")

    assert manager.get_pending_task("user-1", "session-a") is None
    assert manager.get_pending_task("user-1", "session-b") == {"task_id": "task-b"}
