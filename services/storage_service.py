import json
from pathlib import Path

from core.config import settings
from models.schemas import NoteItem


class StorageService:
    def load_sample_notes(self) -> list[NoteItem]:
        file_path = Path(settings.sample_note_path)
        if not file_path.exists():
            return self._default_notes()

        with file_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return [NoteItem(**item) for item in data]

    @staticmethod
    def _default_notes() -> list[NoteItem]:
        return [
            NoteItem(
                title="3个平价防晒真实测评",
                content="油皮夏天真的怕闷痘，这次测了3款热门防晒。",
                likes=3200,
                favorites=1800,
                comments=260,
                tags=["防晒", "测评", "学生党"],
            ),
            NoteItem(
                title="通勤淡妆5分钟搞定",
                content="早八人必看，底妆轻薄不斑驳。",
                likes=2100,
                favorites=1400,
                comments=190,
                tags=["通勤妆", "化妆教程"],
            ),
        ]

