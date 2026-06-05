from pydantic import BaseModel, Field

from models.schemas import NoteItem


class SearchCrawlRequest(BaseModel):
    keywords: list[str] = Field(..., min_length=1, description="搜索关键词列表，至少填一个")
    topic_words: list[str] = Field(default_factory=list, description="话题词列表，正文/标题至少包含其中一个；为空则不过滤")
    min_comments: int = Field(0, ge=0, description="评论数最小值（>=）")
    min_likes: int = Field(0, ge=0, description="点赞数最小值（>=）")
    min_favorites: int = Field(0, ge=0, description="收藏数最小值（>=）")
    target_count: int = Field(20, ge=1, description="目标采集条数")
    content_type: str = "图文"
    detail_mode: str = Field("all", description="all=搜索后补详情，none=只返回搜索卡片摘要")


LocalSiteNoteCard = NoteItem


class SearchCrawlResponse(BaseModel):
    target_count: int
    count: int
    used_keywords: list[str]
    items: list[NoteItem]
