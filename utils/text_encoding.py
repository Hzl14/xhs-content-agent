from __future__ import annotations


_MOJIBAKE_MARKERS = (
    "\ufffd",
    "Ã",
    "Â",
    "鑰",
    "冪",
    "爺",
    "涓",
    "婂",
    "哺",
    "澶",
    "鐢",
    "鐪",
    "鍒",
    "灏",
    "忕",
    "孩",
    "涔",
    "鍥",
    "炬",
    "瑙",
    "嗛",
)


def repair_mojibake(text: str) -> str:
    """Repair common UTF-8 text that was accidentally decoded as GBK/cp1252."""
    if not text:
        return text

    original = text.strip()
    candidates = [original]

    for encoding in ("gbk", "cp936", "latin1", "cp1252"):
        try:
            candidates.append(original.encode(encoding).decode("utf-8"))
        except UnicodeError:
            try:
                candidates.append(original.encode(encoding, errors="replace").decode("utf-8", errors="replace"))
            except UnicodeError:
                continue

    best = min(candidates, key=_mojibake_score).strip()
    return best.replace("\ufffd", "").strip()


def _mojibake_score(text: str) -> tuple[int, int, int]:
    marker_count = sum(text.count(marker) for marker in _MOJIBAKE_MARKERS)
    question_count = text.count("?")
    cjk_count = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    return marker_count * 10 + question_count, -cjk_count, len(text)
