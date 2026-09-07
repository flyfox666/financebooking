"""有数 LedgerAI 桌面版启动器（PyInstaller 打包入口）。

双击后：
1. 若服务已运行 → 直接打开浏览器（多开不会重复起服务）；
2. 否则启动内嵌 FastAPI 服务，并自动打开默认浏览器访问前端界面。
关闭本窗口即退出服务（后台托盘化留待后续）。
"""

import socket
import threading
import webbrowser
import os
import json
import uuid
import urllib.request

HOST = "127.0.0.1"
PORT = 8000
APP_URL = f"http://{HOST}:{PORT}/app"


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex((host, port)) == 0


def main() -> None:
    from app.core.config import DATA_DIR
    ident_file = DATA_DIR / '.instance_id'
    if not ident_file.exists():
        ident_file.write_text(uuid.uuid4().hex, encoding='utf-8')
    instance = ident_file.read_text(encoding='utf-8').strip()
    os.environ['LEDGER_INSTANCE_ID'] = instance
    port = None
    for candidate in range(PORT, PORT + 20):
        if _port_in_use(HOST, candidate):
            try:
                with urllib.request.urlopen(f'http://{HOST}:{candidate}/api/health', timeout=1) as response:
                    health = json.load(response)
                if health.get('app') == 'LedgerAI' and health.get('instance') == instance:
                    webbrowser.open(f'http://{HOST}:{candidate}/app')
                    return
            except Exception:
                pass
        elif port is None:
            port = candidate
    if port is None:
        raise RuntimeError('没有可用端口，请关闭冲突程序后再启动')
    app_url = f'http://{HOST}:{port}/app'

    import uvicorn
    from app.main import app

    def _open_browser_later() -> None:
        for _ in range(60):
            try:
                with urllib.request.urlopen(f'http://{HOST}:{port}/api/health', timeout=1) as response:
                    if json.load(response).get('instance') == instance:
                        webbrowser.open(app_url)
                        return
            except Exception:
                threading.Event().wait(0.5)

    threading.Thread(target=_open_browser_later, daemon=True).start()
    print(f"有数 LedgerAI 正在启动：{app_url}；数据目录：{DATA_DIR.resolve()}")
    print("首次启动会自动初始化数据目录与管理员账号（admin / admin123456）。")
    print("提示：关闭本窗口即退出服务。数据保存在系统用户目录，重装/升级不丢失。")
    uvicorn.run(app, host=HOST, port=port, log_level="info")


if __name__ == "__main__":
    main()
