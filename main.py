from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from api.middleware import register_middlewares
from api.routes import router
from api.xhs_service_routes import router as xhs_service_router
from core.config import settings


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
    description="Refactored XHS agent scaffold without LangChain.",
)

register_middlewares(app)
app.include_router(router)
app.include_router(xhs_service_router)

Path("data/output/images").mkdir(parents=True, exist_ok=True)
Path("data/output/drafts").mkdir(parents=True, exist_ok=True)
app.mount("/images", StaticFiles(directory="data/output/images"), name="images")
app.mount("/drafts", StaticFiles(directory="data/output/drafts"), name="drafts")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/static/index.html")
