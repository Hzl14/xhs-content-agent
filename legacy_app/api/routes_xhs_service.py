"""
routes_xhs_service.py
绠＄悊灏忕孩涔?MCP 杩涚▼鐨勭敓鍛藉懆鏈燂細鍚姩銆佸仠姝€佺姸鎬佹煡璇€?
  POST /xhs-service/start   鍚姩 xiaohongshu-mcp 浜岃繘鍒?  POST /xhs-service/stop    鍋滄杩涚▼
  GET  /xhs-service/status  鏌ヨ杩愯鐘舵€?"""

import socket
import subprocess
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter

from legacy_app.core.config import settings

router = APIRouter(prefix="/xhs-service", tags=["XHS Service"])

# 鍏ㄥ眬杩涚▼鍙ユ焺
_proc: Optional[subprocess.Popen] = None


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """妫€鏌ョ鍙ｆ槸鍚﹀湪鐩戝惉銆?""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _is_running() -> bool:
    """杩涚▼瀛樻椿 涓?绔彛鍙揪 鈫?璁や负鏈嶅姟姝ｅ湪杩愯銆?""
    global _proc
    if _proc is not None and _proc.poll() is None:
        return True
    # 鍗充娇涓嶆槸鏈繘绋嬪惎鍔紝涔熸娴嬬鍙?    return _port_open("127.0.0.1", 18060)


@router.get("/status", summary="鏌ヨ MCP 鏈嶅姟杩愯鐘舵€?)
def get_status() -> dict:
    return {
        "running": _is_running(),
        "port": 18060,
        "binary": settings.xhs_mcp_binary,
    }


@router.post("/start", summary="鍚姩 MCP 鏈嶅姟")
def start_service(headless: bool = True) -> dict:
    global _proc

    if _is_running():
        return {"success": True, "message": "鏈嶅姟宸插湪杩愯", "running": True}

    binary = settings.xhs_mcp_binary
    if not binary:
        return {"success": False, "message": "鏈厤缃?XHS_MCP_BINARY 璺緞", "running": False}

    try:
        args = [binary]
        if not headless:
            args.append("-headless=false")

        # 鍦ㄤ簩杩涘埗鎵€鍦ㄧ洰褰曞惎鍔紝纭繚 cookies.json 璺緞姝ｇ‘
        cwd = str(Path(binary).parent)

        # Windows 涓嬬敤 CREATE_NEW_CONSOLE 璁╂祻瑙堝櫒绐楀彛鐙珛寮瑰嚭
        kwargs: dict = {"cwd": cwd}
        if sys.platform == "win32" and not headless:
            kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE

        _proc = subprocess.Popen(args, **kwargs)

        # 绛夊緟鏈€澶?8 绉掕绔彛灏辩华
        for _ in range(16):
            import time
            time.sleep(0.5)
            if _port_open("127.0.0.1", 18060):
                return {"success": True, "message": "MCP 鏈嶅姟宸插惎鍔?, "running": True}

        return {"success": False, "message": "杩涚▼宸插惎鍔ㄤ絾绔彛鏈氨缁紝璇风◢鍚庨噸璇?, "running": False}

    except FileNotFoundError:
        return {"success": False, "message": f"鎵句笉鍒颁簩杩涘埗鏂囦欢: {binary}", "running": False}
    except Exception as e:
        return {"success": False, "message": f"鍚姩澶辫触: {e}", "running": False}


@router.post("/login", summary="杩愯鐧诲綍宸ュ叿锛堝脊鍑烘祻瑙堝櫒鎵爜锛?)
def run_login() -> dict:
    binary = settings.xhs_mcp_binary
    if not binary:
        return {"success": False, "message": "鏈厤缃?XHS_MCP_BINARY 璺緞"}

    # 鐧诲綍浜岃繘鍒朵笌 MCP 浜岃繘鍒跺悓鐩綍锛屽彧鏄枃浠跺悕涓嶅悓
    binary_dir = Path(binary).parent
    login_candidates = list(binary_dir.glob("*login*"))
    if not login_candidates:
        return {"success": False, "message": f"鏈壘鍒扮櫥褰曞伐鍏凤紙鍦?{binary_dir} 鎼滅储 *login*锛?}

    login_bin = str(login_candidates[0])
    try:
        kwargs: dict = {"cwd": str(binary_dir)}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
        subprocess.Popen([login_bin], **kwargs)
        return {"success": True, "message": f"宸插惎鍔ㄧ櫥褰曞伐鍏凤紝璇峰湪寮瑰嚭鐨勬祻瑙堝櫒涓壂鐮佺櫥褰?, "binary": login_bin}
    except Exception as e:
        return {"success": False, "message": f"鍚姩鐧诲綍宸ュ叿澶辫触: {e}"}


@router.post("/stop", summary="鍋滄 MCP 鏈嶅姟")
def stop_service() -> dict:
    global _proc

    if _proc is not None and _proc.poll() is None:
        _proc.terminate()
        try:
            _proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _proc.kill()
        _proc = None
        return {"success": True, "message": "鏈嶅姟宸插仠姝?, "running": False}

    return {"success": True, "message": "鏈嶅姟鏈繍琛?, "running": False}

