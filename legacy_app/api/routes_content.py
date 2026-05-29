from fastapi import APIRouter, HTTPException

from legacy_app.models.schemas import ContentGenerateRequest, ContentGenerateResponse
from legacy_app.services.content_service import generate_contents

router = APIRouter(prefix="/content", tags=["Content"])


@router.post("/generate", response_model=ContentGenerateResponse)
def generate_content_endpoint(request: ContentGenerateRequest):
    try:
        return generate_contents(request)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
