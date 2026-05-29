from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from core.agent_base import BaseAgent
from models.prompts import NOTE_FILTER_PROMPT
from models.schemas import NodeTrace, NoteItem, PipelineState
from services.llm_service import LLMService
from services.storage_service import StorageService
from services.trace_service import begin_span
from utils.text_encoding import repair_mojibake


class CrawlerAgent(BaseAgent):
    name = "crawler"

    ROLE_WEIGHTS = {
        "core": 1.0,
        "modifier": 0.8,
        "longtail": 0.6,
    }

    AD_SIGNALS = [
        "点击主页",
        "私信领取",
        "限时优惠",
        "扫码",
        "品牌合作",
        "合作推广",
        "广告",
        "下单",
    ]

    SUMMARY_TOTAL_TIMEOUT_SECONDS = 70
    SUMMARY_KEYWORD_TIMEOUT_SECONDS = 65
    RECLAIM_TIMEOUT_SECONDS = 15
    DETAIL_TOTAL_TIMEOUT_SECONDS = 80
    DETAIL_OUTER_TIMEOUT_SECONDS = 90
    DETAIL_ENRICH_CONCURRENCY = 4
    LLM_FILTER_TIMEOUT_SECONDS = 25
    MIN_FINAL_NOTE_COUNT = 1
    MAX_FINAL_NOTE_COUNT = 8
    MAX_SOURCE_NOTE_COUNT = 30
    XHS_STATE_FILE = Path("data/raw/xhs_state.json")
    MAX_LOGIN_STATE_AGE_SECONDS = 24 * 60 * 60

    def __init__(self, storage_service: StorageService, llm_service: LLMService | None = None) -> None:
        self.storage_service = storage_service
        self.llm_service = llm_service

    async def _execute(self, state: PipelineState, trace: NodeTrace) -> PipelineState:
        if state.search_query:
            state.metadata["search_query"] = state.search_query
        if state.search_keywords:
            state.metadata["search_keywords"] = state.search_keywords
        state.final_note_limit = self._effective_final_note_limit(state)
        state.min_final_note_count = self._effective_min_note_count(state)
        state.metadata["raw_crawl_limit"] = state.raw_crawl_limit
        state.metadata["final_note_limit"] = state.final_note_limit
        state.metadata["min_final_note_count"] = state.min_final_note_count

        candidate_notes = state.candidate_notes or []
        if candidate_notes:
            state.metadata["candidate_note_count"] = len(candidate_notes)
            state.metadata["candidate_note_source"] = "provided"
        else:
            candidate_notes = await self._crawl_candidates(state)
            if candidate_notes:
                state.candidate_notes = candidate_notes
                state.metadata["candidate_note_count"] = len(candidate_notes)
                state.metadata["candidate_note_source"] = "crawler"

        if not candidate_notes:
            state.metadata["candidate_note_count"] = 0

        # 规则粗筛：去重 + 过滤 + 排序 → 约 20 条卡片摘要
        rule_filtered = self._filter_candidates(candidate_notes, state)
        topic_rule_filtered = CrawlerAgent._filter_by_required_topic_terms(rule_filtered, state)
        if topic_rule_filtered:
            state.metadata["rule_topic_filtered_count"] = len(topic_rule_filtered)
            rule_filtered = topic_rule_filtered
        state.metadata["rule_filtered_count"] = len(rule_filtered)

        if CrawlerAgent._allows_summary_only_notes(state):
            selected_notes = rule_filtered[: state.final_note_limit]
            state.metadata["llm_filter_used"] = False
        else:
            # LLM 精筛：约 20 → 12 条，同时打 style_tag / quality_signals
            topic = state.search_query or " ".join(state.search_keywords)
            llm_selected = await self._llm_filter(rule_filtered, topic, state.final_note_limit)
            selected_notes = llm_selected or rule_filtered[: state.final_note_limit]
            if llm_selected:
                state.metadata["llm_filter_used"] = True
            else:
                state.metadata["llm_filter_used"] = False

        enriched_notes = await self._enrich_selected_notes(selected_notes, state)
        source_notes = enriched_notes or selected_notes
        complete_notes = [
            note for note in (enriched_notes or selected_notes)
            if (note.content or "").strip()
        ]
        state.metadata["input_note_incomplete_dropped"] = len(enriched_notes or selected_notes) - len(complete_notes)
        topic_filtered_notes = CrawlerAgent._filter_by_required_topic_terms(complete_notes, state)
        state.metadata["input_note_topic_filtered_dropped"] = len(complete_notes) - len(topic_filtered_notes)
        complete_notes = topic_filtered_notes
        if len(complete_notes) < state.min_final_note_count:
            summary_notes = [
                note for note in CrawlerAgent._filter_by_required_topic_terms(source_notes, state)
                if (note.title or "").strip()
            ]
            if summary_notes:
                state.metadata["input_note_summary_fallback_used"] = True
                state.metadata["input_note_summary_fallback_count"] = len(summary_notes)
                complete_notes = summary_notes
        state.input_notes = complete_notes[: state.final_note_limit]
        state.metadata["input_note_count"] = len(state.input_notes)
        required_note_count = 1 if state.plan.needs_content_generation else state.min_final_note_count
        if len(state.input_notes) < required_note_count:
            hint = CrawlerAgent._crawler_failure_hint(state)
            raise ValueError(
                "Crawler did not collect enough complete notes: "
                f"{len(state.input_notes)}/{required_note_count}. "
                f"{hint}"
            )
        trace.status = "success"
        return state

    @staticmethod
    def _crawler_failure_hint(state: PipelineState) -> str:
        errors = " ".join(str(item) for item in state.metadata.get("crawler_errors", []))
        errors = " ".join([errors, str(state.metadata.get("crawler_error", ""))]).lower()
        if "xiaohongshu_login_expired" in errors or "xiaohongshu_login_required" in errors:
            return (
                "Xiaohongshu login state is missing or expired. "
                "Click the login button, finish QR-code login, then retry."
            )
        if state.metadata.get("candidate_note_count") == 0:
            return (
                "No search cards were collected. Please refresh the login state, "
                "try broader keywords, or retry later if Xiaohongshu is rate-limiting the browser."
            )
        return "Please refresh the login state or broaden the search keywords."

    @staticmethod
    def _effective_final_note_limit(state: PipelineState) -> int:
        requested = int(state.final_note_limit or CrawlerAgent.MAX_FINAL_NOTE_COUNT)
        max_limit = (
            CrawlerAgent.MAX_SOURCE_NOTE_COUNT
            if CrawlerAgent._allows_summary_only_notes(state)
            else CrawlerAgent.MAX_FINAL_NOTE_COUNT
        )
        return max(CrawlerAgent.MIN_FINAL_NOTE_COUNT, min(requested, max_limit))

    @staticmethod
    def _allows_summary_only_notes(state: PipelineState) -> bool:
        route = state.metadata.get("delivery_route")
        route_deliverable = route.get("final_deliverable") if isinstance(route, dict) else ""
        return state.plan.intent == "crawl_only" or route_deliverable == "source_notes"

    @staticmethod
    def _effective_min_note_count(state: PipelineState) -> int:
        requested = int(state.min_final_note_count or CrawlerAgent.MIN_FINAL_NOTE_COUNT)
        return max(1, min(requested, state.final_note_limit))

    async def _llm_filter(
        self,
        notes: list[NoteItem],
        topic: str,
        target: int,
    ) -> list[NoteItem]:
        """用 LLM 从规则粗筛后的帖子里精选 target 条，并打 style_tag / quality_signals。"""
        if not self.llm_service or not self.llm_service.enabled or not notes:
            return []

        # 构造精简摘要，避免 token 过多
        notes_summary = []
        for i, note in enumerate(notes):
            notes_summary.append({
                "index": i,
                "title": (note.title or "")[:60],
                "content_preview": (note.content or "")[:120],
                "likes": note.likes,
                "favorites": note.favorites,
                "comments": note.comments,
                "tags": note.tags[:5],
                "publish_time": note.publish_time or "",
            })

        user_prompt = NOTE_FILTER_PROMPT.render_user(
            topic=topic or "小红书热帖",
            notes_json=json.dumps(notes_summary, ensure_ascii=False),
            total=len(notes),
            target=min(target, len(notes)),
        )

        try:
            result = await asyncio.wait_for(
                self.llm_service.chat_json(
                    system=NOTE_FILTER_PROMPT.system,
                    user=user_prompt,
                ),
                timeout=self.LLM_FILTER_TIMEOUT_SECONDS,
            )
            data = json.loads(result.content) if result.content else {}
            selected_items = data.get("selected", [])
            if not isinstance(selected_items, list) or not selected_items:
                return []

            output: list[NoteItem] = []
            for item in selected_items:
                idx = item.get("index")
                if not isinstance(idx, int) or idx < 0 or idx >= len(notes):
                    continue
                note = notes[idx].model_copy()
                note.style_tag = str(item.get("style_tag", "")) or note.style_tag
                signals = item.get("quality_signals", [])
                if isinstance(signals, list):
                    note.quality_signals = [str(s) for s in signals if s]
                output.append(note)
                if len(output) >= target:
                    break

            return output
        except Exception:
            return []

    @staticmethod
    async def _crawl_candidates(state: PipelineState) -> list[NoteItem]:
        keywords = CrawlerAgent._resolve_crawl_keywords(state)
        span = begin_span(
            "tool_call",
            "xhs_crawl_candidates",
            input_summary={
                "keywords": keywords,
                "raw_crawl_limit": state.raw_crawl_limit,
                "final_note_limit": state.final_note_limit,
            },
        )
        if not keywords:
            state.metadata["crawler_skipped_reason"] = "empty_keywords"
            span.end(status="skipped", output_summary={"reason": "empty_keywords", "note_count": 0})
            return []
        if CrawlerAgent._is_login_state_stale():
            state.metadata["crawler_errors"] = [
                "xiaohongshu_login_expired: persisted login state is missing or older than 1 day"
            ]
            span.end(status="failed", output_summary={"note_count": 0}, error="xiaohongshu_login_expired")
            return []

        keyword_plan = CrawlerAgent._build_keyword_plan(keywords, state.raw_crawl_limit)
        state.metadata["keyword_plan"] = keyword_plan

        try:
            from legacy_app.models.schemas import SearchCrawlRequest
            from legacy_app.services.local_site_crawler_service import crawl_local_site_notes
        except Exception as exc:
            state.metadata["crawler_error"] = f"crawler_import_failed: {exc}"
            span.end(status="failed", output_summary={"note_count": 0}, error=f"crawler_import_failed: {exc}")
            return []

        started_at = time.monotonic()

        async def crawl_keyword(item: dict[str, int | str | float]) -> dict:
            keyword = str(item["keyword"])
            target_count = int(item["quota"])
            if target_count <= 0:
                return {"notes": [], "used_keywords": [], "unmet": None, "error": None}
            try:
                request = SearchCrawlRequest(
                    keywords=[keyword],
                    topic_words=[keyword],
                    min_comments=0,
                    min_likes=0,
                    min_favorites=0,
                    target_count=target_count,
                    detail_mode="none",
                )
                response = await asyncio.wait_for(
                    crawl_local_site_notes(request),
                    timeout=CrawlerAgent.SUMMARY_KEYWORD_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                return {
                    "notes": [],
                    "used_keywords": [],
                    "unmet": {"keyword": keyword, "shortfall": target_count},
                    "error": f"{keyword}: {exc}",
                }

            notes = [CrawlerAgent._to_note_item(raw, keyword_type=str(item["role"])) for raw in getattr(response, "items", [])]
            actual_count = len(notes)
            shortfall = max(0, target_count - actual_count)
            return {
                "notes": notes,
                "used_keywords": list(getattr(response, "used_keywords", []) or [keyword]),
                "unmet": {"keyword": keyword, "shortfall": shortfall} if shortfall > 0 else None,
                "error": None,
            }

        tasks = [asyncio.create_task(crawl_keyword(item)) for item in keyword_plan]
        done, pending = await asyncio.wait(
            tasks,
            timeout=CrawlerAgent.SUMMARY_TOTAL_TIMEOUT_SECONDS,
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        collected: list[NoteItem] = []
        used_keywords: list[str] = []
        unmet_keywords: list[dict[str, int | str]] = []
        errors: list[str] = []
        for task in done:
            try:
                result = task.result()
            except Exception as exc:
                errors.append(str(exc))
                continue
            collected.extend(result["notes"])
            used_keywords.extend(result["used_keywords"])
            if result["unmet"]:
                unmet_keywords.append(result["unmet"])
            if result["error"]:
                errors.append(result["error"])

        if pending:
            for item in keyword_plan:
                keyword = str(item["keyword"])
                if keyword not in used_keywords:
                    unmet_keywords.append({"keyword": keyword, "shortfall": int(item["quota"])})
            errors.append(f"summary_timeout_after_{CrawlerAgent.SUMMARY_TOTAL_TIMEOUT_SECONDS}s")

        state.metadata["crawler_parallel_keywords"] = len(tasks)
        state.metadata["crawler_summary_elapsed_seconds"] = round(time.monotonic() - started_at, 2)
        if errors:
            state.metadata["crawler_errors"] = errors

        deduped_before_reclaim = CrawlerAgent._deduplicate_notes(collected)
        if any("xiaohongshu_login_" in error for error in errors):
            output = deduped_before_reclaim[: state.raw_crawl_limit]
            span.end(
                status="failed",
                output_summary={
                    "collected_count": len(collected),
                    "deduped_count": len(output),
                    "used_keywords": used_keywords,
                    "error_count": len(errors),
                },
                error="; ".join(errors[:3]),
            )
            return output
        if unmet_keywords and len(deduped_before_reclaim) < state.final_note_limit:
            try:
                reclaimed_notes = await asyncio.wait_for(
                    CrawlerAgent._reclaim_unfilled_quota(
                        state=state,
                        unmet_keywords=unmet_keywords,
                        keyword_plan=keyword_plan,
                        crawl_fn=crawl_local_site_notes,
                        request_cls=SearchCrawlRequest,
                    ),
                    timeout=CrawlerAgent.RECLAIM_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                state.metadata["crawler_reclaim_error"] = str(exc)
                reclaimed_notes = []
            collected.extend(reclaimed_notes)

        deduped = CrawlerAgent._deduplicate_notes(collected)[: state.raw_crawl_limit]
        state.metadata["crawler_used_keywords"] = used_keywords
        state.metadata["crawler_reclaimed"] = len(deduped) - min(len(collected), state.raw_crawl_limit)
        span.end(
            output_summary={
                "collected_count": len(collected),
                "deduped_count": len(deduped),
                "used_keywords": used_keywords,
                "unmet_keywords": unmet_keywords,
                "error_count": len(errors),
                "elapsed_seconds": state.metadata.get("crawler_summary_elapsed_seconds"),
            },
            error="; ".join(errors[:3]) if errors else None,
            status="failed" if any("xiaohongshu_login_" in error for error in errors) else "success",
        )
        return deduped

    @staticmethod
    def _is_login_state_stale() -> bool:
        state_file = CrawlerAgent.XHS_STATE_FILE
        if not state_file.exists():
            return True
        age_seconds = time.time() - state_file.stat().st_mtime
        return age_seconds > CrawlerAgent.MAX_LOGIN_STATE_AGE_SECONDS

    @staticmethod
    async def _reclaim_unfilled_quota(
        state: PipelineState,
        unmet_keywords: list[dict[str, int | str]],
        keyword_plan: list[dict[str, int | str | float]],
        crawl_fn,
        request_cls,
    ) -> list[NoteItem]:
        overflow = sum(int(item["shortfall"]) for item in unmet_keywords)
        if overflow <= 0:
            return []

        ranked_keywords = sorted(
            keyword_plan,
            key=lambda item: (float(item["weight"]), -int(item["quota"])),
            reverse=True,
        )

        reclaimed: list[NoteItem] = []
        for item in ranked_keywords:
            keyword = str(item["keyword"])
            if overflow <= 0:
                break

            extra_quota = min(overflow, max(2, int(item["quota"]) // 2))
            try:
                request = request_cls(
                    keywords=[keyword],
                    topic_words=[keyword],
                    min_comments=0,
                    min_likes=0,
                    min_favorites=0,
                    target_count=extra_quota,
                    detail_mode="none",
                )
                response = await crawl_fn(request)
            except Exception:
                continue

            notes = [
                CrawlerAgent._to_note_item(raw, keyword_type=str(item["role"]))
                for raw in getattr(response, "items", [])
            ]
            reclaimed.extend(notes)
            overflow -= len(notes)
            if len(CrawlerAgent._deduplicate_notes(reclaimed)) >= state.raw_crawl_limit:
                break

        return reclaimed

    @staticmethod
    def _resolve_crawl_keywords(state: PipelineState) -> list[str]:
        keywords = [repair_mojibake(kw) for kw in state.search_keywords if kw.strip()]
        keywords = CrawlerAgent._expand_search_keywords(keywords)
        unique: list[str] = []
        seen: set[str] = set()
        for keyword in keywords:
            if not keyword:
                continue
            lowered = keyword.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            unique.append(keyword)
            if len(unique) >= 6:
                break
        return unique

    @staticmethod
    def _expand_search_keywords(keywords: list[str]) -> list[str]:
        expanded: list[str] = []

        def add(keyword: str) -> None:
            keyword = keyword.strip()
            if keyword and keyword not in expanded:
                expanded.append(keyword)

        for keyword in keywords:
            add(keyword)
            lowered = keyword.lower()
            if "微调" in keyword:
                add("大模型微调")
                add("LLM微调")
            if "后训练" in keyword or "post training" in lowered or "post-training" in lowered:
                add("大模型后训练")
                add("模型后训练")
            if "llm" in lowered or "大模型" in keyword:
                add("大模型学习")
        return expanded

    @staticmethod
    def _build_keyword_plan(keywords: list[str], total_limit: int) -> list[dict[str, int | str | float]]:
        if not keywords:
            return []

        roles = [CrawlerAgent._keyword_role(index, len(keywords)) for index, _ in enumerate(keywords)]
        base_quota = min(6, max(3, total_limit // max(len(keywords), 1)))
        remaining = max(0, total_limit - base_quota * len(keywords))
        weight_sum = sum(CrawlerAgent.ROLE_WEIGHTS[role] for role in roles)

        plan: list[dict[str, int | str | float]] = []
        assigned = 0
        for index, keyword in enumerate(keywords):
            role = roles[index]
            weight = CrawlerAgent.ROLE_WEIGHTS[role]
            extra = int(round(remaining * (weight / weight_sum))) if weight_sum else 0
            quota = base_quota + extra
            plan.append(
                {
                    "keyword": keyword,
                    "role": role,
                    "weight": weight,
                    "quota": quota,
                }
            )
            assigned += quota

        while assigned > total_limit and plan:
            for item in reversed(plan):
                if int(item["quota"]) > 1 and assigned > total_limit:
                    item["quota"] = int(item["quota"]) - 1
                    assigned -= 1

        while assigned < total_limit and plan:
            plan[assigned % len(plan)]["quota"] = int(plan[assigned % len(plan)]["quota"]) + 1
            assigned += 1

        return plan

    @staticmethod
    def _keyword_role(index: int, total: int) -> str:
        if total <= 1 or index == 0:
            return "core"
        if total == 2:
            return "modifier"
        if index == total - 1:
            return "longtail"
        return "modifier"

    @staticmethod
    def _to_note_item(item: object, keyword_type: str | None = None) -> NoteItem:
        if isinstance(item, NoteItem):
            note = item
        else:
            if hasattr(item, "model_dump"):
                data = item.model_dump()
            elif hasattr(item, "dict"):
                data = item.dict()
            else:
                data = dict(item)  # type: ignore[arg-type]
            note = NoteItem(**data)

        if keyword_type and not note.keyword_type:
            note.keyword_type = keyword_type
        return note

    @staticmethod
    async def _enrich_selected_notes(notes: list[NoteItem], state: PipelineState) -> list[NoteItem]:
        if not notes:
            return []

        needs_detail = [note for note in notes if note.url and not (note.content or "").strip()]
        if not needs_detail:
            state.metadata["detail_enrich_skipped_reason"] = "already_has_content"
            return notes

        try:
            from legacy_app.models.schemas import NoteItem as LegacyNoteItem
            from legacy_app.services.local_site_crawler_service import enrich_local_site_note_details
        except Exception as exc:
            state.metadata["detail_enrich_error"] = f"detail_import_failed: {exc}"
            return notes

        legacy_notes: list[LegacyNoteItem] = []
        for note in notes:
            legacy_notes.append(
                LegacyNoteItem(
                    title=note.title,
                    content=note.content or "",
                    likes=note.likes,
                    favorites=note.favorites,
                    comments=note.comments,
                    tags=note.tags,
                    author=note.author,
                    publish_time=note.publish_time,
                    url=note.url,
                    content_type=note.content_type,
                    keyword_used=note.keyword_used,
                )
            )

        keywords = CrawlerAgent._resolve_crawl_keywords(state) or state.search_keywords
        detail_timeout = min(
            CrawlerAgent.DETAIL_TOTAL_TIMEOUT_SECONDS,
            max(45, len(needs_detail) * 10),
        )
        outer_timeout = min(CrawlerAgent.DETAIL_OUTER_TIMEOUT_SECONDS, detail_timeout + 15)
        try:
            enriched_raw = await asyncio.wait_for(
                enrich_local_site_note_details(
                    legacy_notes,
                    keywords=keywords,
                    concurrency=CrawlerAgent.DETAIL_ENRICH_CONCURRENCY,
                    total_timeout=detail_timeout,
                ),
                timeout=outer_timeout,
            )
        except Exception as exc:
            state.metadata["detail_enrich_error"] = f"detail_run_failed: {exc}"
            return notes

        enriched_notes: list[NoteItem] = []
        for original, enriched in zip(notes, enriched_raw, strict=False):
            note = CrawlerAgent._to_note_item(enriched, keyword_type=original.keyword_type)
            note.keyword_type = original.keyword_type
            note.style_tag = original.style_tag
            note.quality_signals = original.quality_signals
            if not note.content:
                note.content = original.content
            if not note.title:
                note.title = original.title
            enriched_notes.append(note)

        if len(enriched_notes) < len(notes):
            enriched_notes.extend(notes[len(enriched_notes):])

        complete_count = sum(1 for note in enriched_notes if (note.content or "").strip())
        state.metadata["detail_enrich_used"] = True
        state.metadata["detail_enriched_count"] = complete_count
        state.metadata["detail_target_count"] = len(notes)
        return enriched_notes

    @staticmethod
    def _filter_candidates(notes: list[NoteItem], state: PipelineState) -> list[NoteItem]:
        keywords = [kw.strip().lower() for kw in state.search_keywords if kw.strip()]
        candidates = CrawlerAgent._deduplicate_notes(notes)
        candidates = [note for note in candidates if CrawlerAgent._passes_rule_filter(note)]

        if keywords:
            matched = [note for note in candidates if CrawlerAgent._matches_keywords(note, keywords)]
            candidates = matched or candidates

        candidates.sort(key=lambda note: CrawlerAgent._rank_score(note, keywords), reverse=True)
        # 规则筛后保留约 20 条供 LLM 精筛，避免把 token 浪费在明显弱相关候选上。
        rule_limit = max(state.final_note_limit, int(state.final_note_limit * 1.75))
        return CrawlerAgent._balance_by_keyword(candidates, keywords, rule_limit)

    @staticmethod
    def _passes_rule_filter(note: NoteItem) -> bool:
        title = (note.title or "").strip()
        if not title:
            return False

        content = (note.content or "").strip()
        text = f"{title} {content}"
        lowered = text.lower()
        if any(signal.lower() in lowered for signal in CrawlerAgent.AD_SIGNALS):
            return False

        if note.content_type and "视频" in note.content_type:
            return False
        if note.video_urls:
            return False

        # content 有内容时才检查长度（Phase 1 摘要 content 为空，跳过此检查）
        if content and (len(content) < 30 or len(content) > 2000):
            return False

        # Phase 1 摘要通常只有点赞，评论/收藏缺失时不要过早杀掉候选。
        if not content and note.likes < 20:
            return False
        if content and note.likes < 3 and note.favorites < 2 and note.comments < 1:
            return False

        return True

    @staticmethod
    def _deduplicate_notes(notes: list[NoteItem]) -> list[NoteItem]:
        seen: set[str] = set()
        unique: list[NoteItem] = []
        for note in notes:
            key = (note.url or note.title or note.content[:40]).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(note)
        return unique

    @staticmethod
    def _matches_keywords(note: NoteItem, keywords: list[str]) -> bool:
        haystack = CrawlerAgent._note_text(note)
        return any(keyword in haystack for keyword in keywords)

    @staticmethod
    def _filter_by_required_topic_terms(notes: list[NoteItem], state: PipelineState) -> list[NoteItem]:
        source_text = " ".join([state.search_query, " ".join(state.search_keywords)])
        required_any_terms: list[str] = []
        if "中科大" in source_text or "中国科学技术大学" in source_text:
            required_any_terms = ["中科大", "中国科学技术大学", "ustc"]

        required_numbers = CrawlerAgent._required_numeric_terms(source_text)
        if not required_any_terms and not required_numbers:
            return notes

        matched = []
        for note in notes:
            text = CrawlerAgent._topic_text(note)
            if required_numbers and not all(number in text for number in required_numbers):
                continue
            if required_any_terms and not any(term in text for term in required_any_terms):
                continue
            matched.append(note)
        if required_numbers:
            return matched
        return matched or notes

    @staticmethod
    def _required_numeric_terms(source_text: str) -> list[str]:
        text = source_text.lower()
        required: list[str] = []
        for number in re.findall(r"(?<!\d)\d{3,6}(?!\d)", text):
            if number in required:
                continue
            context_match = re.search(rf".{{0,8}}{re.escape(number)}.{{0,8}}", text)
            context = context_match.group(0) if context_match else text
            if any(word in context for word in ["考研", "上岸", "经验", "408", "统考", "软微", "科软"]):
                required.append(number)
        return required

    @staticmethod
    def _topic_text(note: NoteItem) -> str:
        return " ".join(
            [
                note.title or "",
                note.content or "",
                " ".join(note.tags or []),
            ]
        ).lower()

    @staticmethod
    def _note_text(note: NoteItem) -> str:
        return " ".join(
            [
                note.title or "",
                note.content or "",
                " ".join(note.tags or []),
                note.keyword_used or "",
            ]
        ).lower()

    @staticmethod
    def _rank_score(note: NoteItem, keywords: list[str]) -> tuple[float, float, float, str]:
        heat_score = note.likes * 0.45 + note.favorites * 0.35 + note.comments * 0.2
        recency_score = CrawlerAgent._recency_score(note.publish_time)
        keyword_score = CrawlerAgent._keyword_match_score(note, keywords)
        total = heat_score + recency_score + keyword_score
        return total, heat_score, keyword_score, note.publish_time or ""

    @staticmethod
    def _keyword_match_score(note: NoteItem, keywords: list[str]) -> float:
        if not keywords:
            return 0.0
        haystack = CrawlerAgent._note_text(note)
        matched = sum(1 for keyword in keywords if keyword in haystack)
        return matched * 80.0

    @staticmethod
    def _recency_score(publish_time: str | None) -> float:
        publish_dt = CrawlerAgent._parse_publish_time(publish_time)
        if publish_dt is None:
            return 0.0

        days_ago = max(0.0, (datetime.now() - publish_dt).total_seconds() / 86400)
        if days_ago < 1:
            return 10.0
        if days_ago <= 30:
            return 60.0 - days_ago
        if days_ago <= 90:
            return 25.0 - (days_ago - 30) * 0.25
        if days_ago <= 365:
            return max(1.0, 8.0 - (days_ago - 90) * 0.02)
        return 0.0

    @staticmethod
    def _parse_publish_time(publish_time: str | None) -> datetime | None:
        if not publish_time:
            return None
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
            try:
                return datetime.strptime(publish_time.strip(), fmt)
            except ValueError:
                continue
        return None

    @staticmethod
    def _balance_by_keyword(notes: list[NoteItem], keywords: list[str], limit: int) -> list[NoteItem]:
        if limit <= 0:
            return []
        if not keywords:
            return notes[:limit]

        selected: list[NoteItem] = []
        selected_keys: set[str] = set()
        keyword_plan = CrawlerAgent._build_keyword_plan(keywords, limit)

        for item in keyword_plan:
            keyword = str(item["keyword"]).lower()
            quota = max(1, int(item["quota"]))
            count = 0
            for note in notes:
                key = note.url or note.title or (note.content or "")[:40]
                if key in selected_keys:
                    continue
                if keyword not in CrawlerAgent._note_text(note):
                    continue
                selected.append(note)
                selected_keys.add(key)
                count += 1
                if count >= quota or len(selected) >= limit:
                    break
            if len(selected) >= limit:
                break

        if len(selected) < limit:
            for note in notes:
                key = note.url or note.title or (note.content or "")[:40]
                if key in selected_keys:
                    continue
                selected.append(note)
                selected_keys.add(key)
                if len(selected) >= limit:
                    break

        return selected[:limit]
