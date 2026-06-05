import os
import asyncio
from types import SimpleNamespace

os.environ["DEBUG"] = "true"

from agents.crawler_agent import CrawlerAgent
from agents.planner_agent import PlannerAgent
from models.schemas import NoteItem, PipelineState
from services.storage_service import StorageService


def _note(index: int) -> NoteItem:
    keyword = "skin care"
    return NoteItem(
        title=f"{keyword} routine test note {index}",
        content=(
            "This is a realistic long-form note with enough body text "
            "to pass the existing crawler rule filter cleanly."
        ),
        likes=100 + index,
        favorites=50 + index,
        comments=10 + index,
        tags=[keyword, "routine"],
        url=f"https://www.xiaohongshu.com/search_result/test-{index}",
        keyword_used=keyword,
    )


def test_provided_candidate_notes_become_input_notes_without_live_crawl(monkeypatch):
    async def fail_live_crawl(state):  # pragma: no cover - failure path assertion
        raise AssertionError("live crawler should not run when candidate notes are provided")

    monkeypatch.setattr(CrawlerAgent, "_crawl_candidates", staticmethod(fail_live_crawl))

    state = PipelineState(
        run_id="crawler-provided-candidates",
        search_query="skin care",
        search_keywords=["skin care"],
        raw_crawl_limit=30,
        final_note_limit=12,
        candidate_notes=[_note(i) for i in range(12)],
    )

    result = asyncio.run(CrawlerAgent(StorageService()).run(state))

    assert not result.failed
    assert len(result.input_notes) == 8
    assert result.final_note_limit == 8
    assert result.metadata["min_final_note_count"] == 1
    assert result.metadata["candidate_note_source"] == "provided"
    assert result.metadata["candidate_note_count"] == 12
    assert result.metadata["input_note_count"] == 8


def test_crawl_only_can_return_summary_cards_without_detail_body(monkeypatch):
    async def fail_live_crawl(state):  # pragma: no cover - failure path assertion
        raise AssertionError("live crawler should not run when candidate notes are provided")

    monkeypatch.setattr(CrawlerAgent, "_crawl_candidates", staticmethod(fail_live_crawl))

    state = PipelineState(
        run_id="crawler-summary-source-notes",
        search_query="考研11408上岸热帖",
        search_keywords=["考研11408", "11408上岸"],
        raw_crawl_limit=20,
        final_note_limit=10,
        candidate_notes=[
            NoteItem(
                title=f"11408 上岸经验热帖 {i}",
                content="",
                likes=100 + i,
                favorites=50 + i,
                comments=5 + i,
                tags=["考研11408"],
                url=f"https://www.xiaohongshu.com/search_result/11408-{i}",
            )
            for i in range(10)
        ],
    )
    state.plan.intent = "crawl_only"

    result = asyncio.run(CrawlerAgent(StorageService()).run(state))

    assert not result.failed
    assert len(result.input_notes) == 10
    assert result.final_note_limit == 10
    assert result.metadata["input_note_summary_fallback_used"] is True


def test_numeric_topic_request_filters_nearby_exam_codes(monkeypatch):
    async def fail_live_crawl(state):  # pragma: no cover - failure path assertion
        raise AssertionError("live crawler should not run when candidate notes are provided")

    monkeypatch.setattr(CrawlerAgent, "_crawl_candidates", staticmethod(fail_live_crawl))

    state = PipelineState(
        run_id="crawler-11408-exact-filter",
        search_query="考研11408上岸",
        search_keywords=["考研11408", "11408上岸"],
        raw_crawl_limit=20,
        final_note_limit=3,
        candidate_notes=[
            NoteItem(
                title="双非一战上岸科软经验贴",
                content="408篇一战成硕，计算机考研经验分享。",
                likes=300,
                favorites=80,
                comments=20,
                tags=["408", "考研"],
                url="https://www.xiaohongshu.com/search_result/408-wrong",
            ),
            NoteItem(
                title="22408计算机考研经验",
                content="这是22408相关经验，专业课代码不同。",
                likes=500,
                favorites=100,
                comments=20,
                tags=["22408", "考研"],
                url="https://www.xiaohongshu.com/search_result/22408-wrong",
            ),
            NoteItem(
                title="11408上岸经验热帖 1",
                content="",
                likes=120,
                favorites=60,
                comments=8,
                tags=["考研11408"],
                url="https://www.xiaohongshu.com/search_result/11408-1",
            ),
            NoteItem(
                title="11408上岸经验热帖 2",
                content="",
                likes=110,
                favorites=55,
                comments=7,
                tags=["考研11408"],
                url="https://www.xiaohongshu.com/search_result/11408-2",
            ),
            NoteItem(
                title="11408上岸经验热帖 3",
                content="",
                likes=100,
                favorites=50,
                comments=6,
                tags=["考研11408"],
                url="https://www.xiaohongshu.com/search_result/11408-3",
            ),
        ],
    )
    state.plan.intent = "crawl_only"

    result = asyncio.run(CrawlerAgent(StorageService()).run(state))

    assert not result.failed
    assert len(result.input_notes) == 3
    assert all("11408" in CrawlerAgent._topic_text(note) for note in result.input_notes)
    assert all("22408" not in CrawlerAgent._topic_text(note) for note in result.input_notes)


def test_ai_training_keywords_expand_for_better_recall():
    state = PipelineState(
        run_id="crawler-ai-keyword-expansion",
        search_query="微调和后训练",
        search_keywords=["微调", "后训练"],
    )

    keywords = CrawlerAgent._resolve_crawl_keywords(state)

    assert "微调" in keywords
    assert "大模型微调" in keywords
    assert "LLM微调" in keywords
    assert "大模型后训练" in keywords


def test_crawler_fails_when_complete_notes_are_below_minimum(monkeypatch):
    async def empty_live_crawl(state):
        return []

    def fail_sample_notes(self):  # pragma: no cover - failure path assertion
        raise AssertionError("sample notes should not be used as crawler success data")

    monkeypatch.setattr(CrawlerAgent, "_crawl_candidates", staticmethod(empty_live_crawl))
    monkeypatch.setattr(StorageService, "load_sample_notes", fail_sample_notes)

    state = PipelineState(
        run_id="crawler-empty-candidates",
        search_query="skin care",
        search_keywords=["skin care"],
    )

    result = asyncio.run(CrawlerAgent(StorageService()).run(state))

    assert result.failed
    assert "did not collect enough complete notes" in result.error_message
    assert result.metadata["candidate_note_count"] == 0
    assert "candidate_note_source" not in result.metadata


def test_crawler_failure_message_mentions_expired_login(monkeypatch):
    async def expired_login_crawl(state):
        state.metadata["crawler_errors"] = ["微调: xiaohongshu_login_expired: persisted login state is no longer valid"]
        return []

    monkeypatch.setattr(CrawlerAgent, "_crawl_candidates", staticmethod(expired_login_crawl))

    state = PipelineState(
        run_id="crawler-expired-login",
        search_query="微调和后训练",
        search_keywords=["微调", "后训练"],
    )

    result = asyncio.run(CrawlerAgent(StorageService()).run(state))

    assert result.failed
    assert "did not collect enough complete notes" in result.error_message
    assert "login state is missing or expired" in result.error_message


def test_stale_login_state_fails_fast_before_live_crawl(monkeypatch):
    async def fail_live_crawl(state):  # pragma: no cover - failure path assertion
        raise AssertionError("live crawler should not run with stale login state")

    monkeypatch.setattr(CrawlerAgent, "_is_login_state_stale", staticmethod(lambda: True))

    state = PipelineState(
        run_id="crawler-stale-login-fast-fail",
        search_query="微调和后训练",
        search_keywords=["微调", "后训练"],
    )

    result = asyncio.run(CrawlerAgent(StorageService()).run(state))

    assert result.failed
    assert "login state is missing or expired" in result.error_message


def test_phase1_card_extractor_uses_fallback_selectors():
    from services.local_site_crawler_service import _JS_EXTRACT_CARDS, _JS_GET_HREFS

    assert 'a[href*="/explore/"]' in _JS_EXTRACT_CARDS
    assert "aria-label" in _JS_EXTRACT_CARDS
    assert "img" in _JS_EXTRACT_CARDS
    assert 'a[href*="/explore/"]' in _JS_GET_HREFS


def test_two_keyword_summary_crawl_runs_in_parallel(monkeypatch):
    from services import local_site_crawler_service

    active = 0
    max_active = 0

    async def fake_crawl(request):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.05)
        active -= 1
        keyword = request.keywords[0]
        items = [
            NoteItem(
                title=f"{keyword} note {i}",
                content="",
                likes=100 + i,
                favorites=0,
                comments=0,
                tags=[keyword],
                url=f"https://www.xiaohongshu.com/search_result/{keyword}-{i}",
                keyword_used=keyword,
            )
            for i in range(request.target_count)
        ]
        return SimpleNamespace(used_keywords=[keyword], items=items)

    monkeypatch.setattr(local_site_crawler_service, "crawl_local_site_notes", fake_crawl)
    monkeypatch.setattr(CrawlerAgent, "_is_login_state_stale", staticmethod(lambda: False))

    state = PipelineState(
        run_id="crawler-parallel-keywords",
        search_keywords=["skin care", "routine"],
        raw_crawl_limit=4,
        final_note_limit=2,
    )

    notes = asyncio.run(CrawlerAgent._crawl_candidates(state))

    assert max_active == 2
    assert len(notes) == 4
    assert state.metadata["crawler_parallel_keywords"] == 2


def test_planner_runs_full_pipeline_when_notes_are_already_provided():
    class DisabledLLM:
        enabled = False

    state = PipelineState(
        run_id="planner-provided-candidates",
        user_message="",
        candidate_notes=[_note(i) for i in range(12)],
    )

    result = asyncio.run(PlannerAgent(DisabledLLM()).run(state))

    assert not result.failed
    assert not result.plan.needs_clarification
    assert result.plan.intent == "full_post"
    assert result.plan.needs_crawl
    assert result.plan.needs_analysis
    assert result.plan.needs_topic_generation
    assert result.plan.needs_content_generation
