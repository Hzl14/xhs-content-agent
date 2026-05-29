from __future__ import annotations

import re

from models.schemas import ContentItem


MARKDOWN_LEAK_PATTERN = re.compile(
    r"(\*\*[^*\n]+\*\*|^#{1,6}\s+|```|`[^`\n]+`|\[[^\]]+\]\([^)]+\))",
    flags=re.MULTILINE,
)


def has_markdown_leak(text: str) -> bool:
    return bool(MARKDOWN_LEAK_PATTERN.search(text or ""))


def clean_markdown_syntax(text: str) -> str:
    """Remove Markdown that Xiaohongshu will display as raw text."""
    if not text:
        return text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", text)
    text = re.sub(r"(?m)^#{1,6}\s*", "", text)
    text = text.replace("```", "").replace("`", "")
    return text.strip()


def clean_content_item(content: ContentItem) -> ContentItem:
    return content.model_copy(
        update={
            "title": clean_markdown_syntax(content.title),
            "body": clean_markdown_syntax(content.body),
            "cta": clean_markdown_syntax(content.cta),
            "hashtags": [clean_markdown_syntax(tag).lstrip("#") for tag in content.hashtags if tag.strip()],
            "image_suggestion": clean_markdown_syntax(content.image_suggestion),
            "content_type": clean_markdown_syntax(content.content_type),
        }
    )
