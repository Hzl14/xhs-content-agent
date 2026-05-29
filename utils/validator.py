from models.schemas import ContentItem


def content_has_basic_quality(content: ContentItem) -> bool:
    return len(content.body) >= 80 and len(content.hashtags) >= 2 and bool(content.cta.strip())

