import re
from collections import Counter

try:
    import jieba
except Exception:  # noqa: BLE001
    jieba = None


STOPWORDS = {"的", "了", "和", "是", "我", "你", "就", "都", "很", "在", "一个"}


def cut_words(text: str) -> list[str]:
    if jieba:
        words = jieba.lcut(text)
    else:
        words = re.split(r"\W+", text)
    return [w.strip() for w in words if w.strip()]


def top_keywords(texts: list[str], k: int = 10) -> list[str]:
    counter = Counter()
    for text in texts:
        for word in cut_words(text):
            if len(word) < 2 or word in STOPWORDS:
                continue
            counter[word] += 1
    return [w for w, _ in counter.most_common(k)]

