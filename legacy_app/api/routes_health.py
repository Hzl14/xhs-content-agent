from fastapi import APIRouter
from legacy_app.core.config import settings
from legacy_app.models.schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["Health"])
def health_check():
    return HealthResponse(
        status="ok",
        app_name=settings.app_name,
        version=settings.app_version
    )

"""
### 浠€涔堟儏鍐典笅 status 浼氫笉鏄?"ok"
鍦ㄦ墿灞曠殑鍋ュ悍妫€鏌ヤ腑锛屼互涓嬫儏鍐典細瀵艰嚧 status 涓嶆槸 "ok"锛?
1. 鏁版嵁搴撹繛鎺ュけ璐?2. 澶栭儴 API 璋冪敤澶辫触 锛堝 DashScope/Qwen API锛?3. 缂撳瓨鏈嶅姟涓嶅彲鐢?4. 纾佺洏绌洪棿涓嶈冻
5. 鍐呭瓨浣跨敤杩囬珮
6. 鍏朵粬鍏抽敭鏈嶅姟涓嶅彲鐢?"""

