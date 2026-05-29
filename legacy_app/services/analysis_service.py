from collections import Counter
from typing import List
import re

import jieba

from legacy_app.models.schemas import (
    NoteItem,
    ScoredNoteItem,
    AnalyzeResponse,
    TitleFeatureStats,
)


STOPWORDS = {
    "鐨?, "浜?, "鍜?, "鏄?, "鎴?, "涔?, "寰?, "閮?, "灏?, "鍙?, "澶?,
    "鍦?, "鐪熺殑", "涓€涓?, "杩?, "鍑犳", "閫傚悎", "鎬庝箞", "鍒板簳", "涓€涓?,
    "浠ュ強", "鎴戜滑", "浣犱滑", "浠栦滑", "鑷繁", "鍙互", "涓嶄細", "灏辨槸"
}

RECOMMENDATION_WORDS = [
    "鎺ㄨ崘", "鍚堥泦", "娴嬭瘎", "閬块浄", "骞虫浛", "蹇呭", "娓呭崟", "鏁欑▼", "鍒嗕韩"
]


def calculate_viral_score(note: NoteItem) -> float:
    """
    Calculate a simple viral score based on engagement.
    """
    return note.likes * 0.4 + note.favorites * 0.4 + note.comments * 0.2


def extract_title_keywords(notes: List[NoteItem], top_k: int = 10) -> List[str]:
    """
    Extract keywords from Chinese titles using jieba.
    """
    all_words = []

    for note in notes:
        words = jieba.lcut(note.title)
        for word in words:
            cleaned = word.strip()
            if (
                cleaned
                and cleaned not in STOPWORDS
                and len(cleaned) >= 2
                and not re.fullmatch(r"[\W_]+", cleaned)
            ):
                all_words.append(cleaned)

    counter = Counter(all_words)
    return [word for word, _ in counter.most_common(top_k)]


def extract_top_tags(notes: List[NoteItem], top_k: int = 10) -> List[str]:
    """
    Extract top tags from all notes.
    """
    all_tags = []
    for note in notes:
        all_tags.extend(note.tags)

    counter = Counter(all_tags)
    return [tag for tag, _ in counter.most_common(top_k)]


def analyze_title_features(notes: List[NoteItem]) -> TitleFeatureStats:
    """
    Analyze title-level statistical features.
    """
    total_titles = len(notes)
    if total_titles == 0:
        return TitleFeatureStats(
            average_title_length=0,
            titles_with_numbers=0,
            titles_with_recommendation_words=0,
            titles_with_question_marks=0,
        )

    total_length = 0
    titles_with_numbers = 0
    titles_with_recommendation_words = 0
    titles_with_question_marks = 0

    for note in notes:
        title = note.title
        total_length += len(title)

        if re.search(r"\d", title):
            titles_with_numbers += 1

        if any(word in title for word in RECOMMENDATION_WORDS):
            titles_with_recommendation_words += 1

        if "?" in title or "锛? in title:
            titles_with_question_marks += 1

    return TitleFeatureStats(
        average_title_length=round(total_length / total_titles, 2),
        titles_with_numbers=titles_with_numbers,
        titles_with_recommendation_words=titles_with_recommendation_words,
        titles_with_question_marks=titles_with_question_marks,
    )


def extract_title_patterns(notes: List[NoteItem], top_k: int = 10) -> List[str]:
    """
    Extract frequent pattern words appearing in titles.
    """
    pattern_counter = Counter()

    for note in notes:
        title = note.title
        for word in RECOMMENDATION_WORDS:
            if word in title:
                pattern_counter[word] += 1

    return [word for word, _ in pattern_counter.most_common(top_k)]


def generate_insight_points(
    notes: List[NoteItem],
    top_keywords: List[str],
    top_tags: List[str],
    title_stats: TitleFeatureStats,
    title_patterns: List[str],
) -> List[str]:
    """
    Generate rule-based insight points for business interpretation.
    """
    insights = []

    if top_tags:
        insights.append(f"楂橀鏍囩闆嗕腑鍦細{', '.join(top_tags[:3])}锛岃鏄庤繖浜涜瘽棰樻洿瀹规槗鍚稿紩鐢ㄦ埛鍏虫敞銆?)

    if top_keywords:
        insights.append(f"楂橀鏍囬鍏抽敭璇嶅寘鎷細{', '.join(top_keywords[:5])}锛屽彲浠ヤ綔涓哄悗缁€夐鐢熸垚鐨勯噸瑕佸弬鑰冦€?)

    if title_patterns:
        insights.append(f"鏍囬涓父瑙佹ā寮忚瘝鏈夛細{', '.join(title_patterns[:5])}锛岃鏄庘€滄帹鑽?娴嬭瘎/閬块浄/鍚堥泦鈥濈被琛ㄨ揪鏇村彈娆㈣繋銆?)

    if title_stats.titles_with_numbers > 0:
        insights.append("閮ㄥ垎楂樿〃鐜版爣棰樺寘鍚暟瀛楋紝璇存槑娓呭崟鍨嬨€佹楠ゅ瀷銆佹暟閲忓瀷琛ㄨ揪鍏锋湁涓€瀹氬惛寮曞姏銆?)

    if title_stats.titles_with_question_marks > 0:
        insights.append("閮ㄥ垎鏍囬浣跨敤闂彞褰㈠紡锛岃鏄庢彁闂紡鏍囬鏈夊姪浜庢縺鍙戣鑰呯偣鍑诲叴瓒ｃ€?)

    avg_score = sum(calculate_viral_score(note) for note in notes) / len(notes) if notes else 0
    insights.append(f"褰撳墠鏍锋湰骞冲潎鐖嗘鍒嗘暟涓?{avg_score:.2f}锛屽彲浣滀负鍚庣画鍐呭浼樺寲鐨勫弬鑰冨熀绾裤€?)

    return insights


def analyze_notes(notes: List[NoteItem], top_n: int = 3) -> AnalyzeResponse:
    """
    Analyze notes and return a richer content-insight report.
    """
    scored_notes = [
        ScoredNoteItem(**note.model_dump(), viral_score=calculate_viral_score(note))
        for note in notes
    ]

    scored_notes.sort(key=lambda x: x.viral_score, reverse=True)

    top_notes = scored_notes[:top_n]
    top_keywords = extract_title_keywords(notes)
    top_tags = extract_top_tags(notes)
    title_stats = analyze_title_features(notes)
    title_patterns = extract_title_patterns(notes)
    insight_points = generate_insight_points(
        notes=notes,
        top_keywords=top_keywords,
        top_tags=top_tags,
        title_stats=title_stats,
        title_patterns=title_patterns,
    )

    summary = (
        f"鍏卞垎鏋?{len(notes)} 鏉″唴瀹广€傞珮琛ㄧ幇鍐呭涓昏闆嗕腑鍦?"
        f"{'銆?.join(top_tags[:3]) if top_tags else '鑻ュ共鐑棬璇濋'}锛?
        f"鏍囬涓父瑙佲€渰'銆?.join(title_patterns[:3]) if title_patterns else '鎺ㄨ崘/娴嬭瘎绫?}鈥濊〃杈撅紝"
        f"鏁翠綋涓婃洿鍋忓悜瀹炵敤寤鸿銆佺粡楠屽垎浜拰闂瑙ｅ喅鍨嬪唴瀹广€?
    )

    return AnalyzeResponse(
        total_count=len(notes),
        top_notes=top_notes,
        top_keywords=top_keywords,
        top_tags=top_tags,
        title_feature_stats=title_stats,
        title_patterns=title_patterns,
        insight_points=insight_points,
        summary=summary,
    )
