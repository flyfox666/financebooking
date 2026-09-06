"""有数 LedgerAI 桌面版启动器（PyInstaller 打包入口）。

双击后：
1. 若服务已运行 → 直接打开浏览器（多开不会重复起服务）；
2. 否则启动内嵌 FastAPI 服务，并自动打开默认浏览器访问前端界面。
关闭本窗口即退出服务（后台托盘化留待后续）。
"""

import socket
import threading
import webbrowser

HOST = "127.0.0.1"
PORT = 8000
APP_URL = f"http://{HOST}:{PORT}/app"


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex((host, port)) == 0


def main() -> None:
    if _port_in_use(HOST, PORT):
        webbrowser.open(APP_URL)
        print(f"有数 LedgerAI 已在运行，已为你打开浏览器：{APP_URL}")
        return

    import uvicorn
    from app.main import app

    def _open_browser_later() -> None:
        threading.Event().wait(2.0)
        webbrowser.open(APP_URL)

    threading.Thread(target=_open_browser_later, daemon=True).start()
    print(f"有数 LedgerAI 正在启动，稍后将自动打开浏览器：{APP_URL}")
    print("首次启动会自动初始化数据目录与管理员账号（admin / admin123456）。")
    print("提示：关闭本窗口即退出服务。数据保存在系统用户目录，重装/升级不丢失。")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()