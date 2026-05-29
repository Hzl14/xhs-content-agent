from functools import lru_cache

from agents.analysis_agent import AnalysisAgent
from agents.content_agent import ContentAgent
from agents.crawler_agent import CrawlerAgent
from agents.planner_agent import PlannerAgent
from agents.publisher_agent import PublisherAgent
from agents.reviewer_agent import ReviewerAgent
from agents.topic_agent import TopicAgent
from core.config import settings
from core.session_manager import SessionManager
from memory.config import MemoryConfig
from memory.manager import ConversationManager
from services.evaluation_service import EvaluationService
from services.draft_service import DraftService
from services.llm_service import LLMService
from services.monitor_service import MonitorService
from services.session_service import SessionService
from services.storage_service import StorageService


class AppContainer:
    def __init__(self) -> None:
        self.session_manager = SessionManager()
        self.storage_service = StorageService()
        self.llm_service = LLMService()
        self.evaluation_service = EvaluationService(self.llm_service)
        self.draft_service = DraftService()
        self.session_service = SessionService(self.session_manager)
        self.monitor_service = MonitorService()

        # ── 记忆模块：从 settings 同步参数 ───────────────────────────────────
        memory_config = MemoryConfig(
            memory_base_dir=settings.memory_base_dir,
            short_term_active_turns=settings.memory_short_term_turns,
            formal_summary_trigger_turns=settings.memory_summary_trigger_turns,
            hard_token_limit=settings.memory_hard_token_limit,
            strong_related_threshold=settings.memory_strong_threshold,
            mid_related_threshold=settings.memory_mid_threshold,
            pattern_feedback_max_active_rules=settings.pattern_feedback_max_active_rules,
            pattern_feedback_max_patterns=settings.pattern_feedback_max_patterns,
            pattern_feedback_resolve_after_successes=settings.pattern_feedback_resolve_after_successes,
            pattern_feedback_compact_every_updates=settings.pattern_feedback_compact_every_updates,
            pattern_feedback_success_threshold=settings.pattern_feedback_success_threshold,
        )
        self.memory_manager = ConversationManager(
            llm_service=self.llm_service,
            config=memory_config,
        )

        self.crawler_agent = CrawlerAgent(self.storage_service, llm_service=self.llm_service)
        self.planner_agent = PlannerAgent(self.llm_service)
        self.analysis_agent = AnalysisAgent()
        self.topic_agent = TopicAgent(self.llm_service)
        self.content_agent = ContentAgent(self.llm_service)
        # ReviewerAgent 注入 memory_manager，pipeline 结束时写回记忆
        self.reviewer_agent = ReviewerAgent(
            self.evaluation_service,
            self.llm_service,
            memory_manager=self.memory_manager,
        )
        self.publisher_agent = PublisherAgent()


@lru_cache(maxsize=1)
def get_container() -> AppContainer:
    return AppContainer()
