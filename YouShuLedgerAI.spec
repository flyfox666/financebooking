# -*- mode: python ; coding: utf-8 -*-
"""有数 LedgerAI 打包配置（PyInstaller，onedir 模式）。

onedir（文件夹）而非 onefile：启动快、方便 Inno Setup 装目录、
便于定位打包遗留问题；绿色压缩包直接 zip 该 dist 文件夹即可。

用法：build\venv\Scripts\pyinstaller YouShuLedgerAI.spec
产物：dist\YouShuLedgerAI\YouShuLedgerAI.exe
"""

a = Analysis(
    ["backend/app/launcher.py"],
    pathex=["backend"],
    binaries=[],
    datas=[
        # 迁移脚本 + 前端静态资源（main.py 打包后用 sys._MEIPASS 定位）
        ("backend/alembic.ini", "."),
        ("backend/alembic", "alembic"),
        ("backend/app/static", "app/static"),
    ],
    hiddenimports=[
        # uvicorn 运行时动态加载的实现（PyInstaller 无法静态追踪）
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.http.httptools_impl",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
        "uvicorn.lifespan.off",
        # Alembic 迁移脚本 import 的公共模块
        "app.models",
        "app.ledger.ai.packs",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="YouShuLedgerAI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # 一期保留控制台（可见日志、关窗即停）；二期托盘化改 False
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="YouShuLedgerAI",
)