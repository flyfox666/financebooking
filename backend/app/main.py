import asyncio
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    attachments,
    ai,
    auth,
    books,
    contacts,
    invoices,
    llm,
    periods,
    reports,
    system,
    tax,
    users,
    vouchers,
)
from app.core.backup import run_backup_now
from app.core.database import SessionLocal

BACKUP_HOUR = 3


def _bundle_root() -> Path:
    """打包后资源根目录：PyInstaller 解压的 _MEIPASS；开发环境 = backend 根。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[1]


def _static_dir() -> Path:
    """前端静态资源目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "app" / "static"
    return Path(__file__).resolve().parent / "static"


def run_migrations() -> None:
    backend_root = _bundle_root()
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    command.upgrade(cfg, "head")


def seed_llm_provider() -> None:
    from app.ledger.llm.gateway import seed_from_env

    with SessionLocal() as db:
        seed_from_env(db)


def seed_default_admin() -> None:
    """空模板首次启动：若无任何用户，自动创建默认管理员 admin。

    打包分发版没有 scripts/init_dev_db.py，需靠此在首次启动建出可登录账号；
    数据落盘后（User 表非空）不再重复创建。
    """
    from sqlalchemy import select

    from app.core.security import hash_password
    from app.models.user import User

    with SessionLocal() as db:
        has_user = db.scalar(select(User.id).limit(1))
        if has_user:
            return
        db.add(
            User(
                username="admin",
                password_hash=hash_password("admin123456"),
                display_name="管理员",
                role="admin",
            )
        )
        db.commit()


async def backup_scheduler():
    while True:
        now = datetime.now()
        target = now.replace(hour=BACKUP_HOUR, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            await asyncio.to_thread(run_backup_now)
        except Exception:
            pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    run_migrations()
    seed_llm_provider()
    seed_default_admin()
    task = asyncio.create_task(backup_scheduler())
    yield
    task.cancel()


app = FastAPI(title="LedgerAI API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(system.router)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(books.router)
app.include_router(contacts.router)
app.include_router(vouchers.router)
app.include_router(periods.router)
app.include_router(reports.router)
app.include_router(attachments.router)
app.include_router(invoices.router)
app.include_router(tax.router)
app.include_router(llm.router)
app.include_router(ai.router)


@app.get("/settings", include_in_schema=False)
def settings_page():
    from fastapi.responses import FileResponse

    return FileResponse(_static_dir() / "index.html")


@app.get("/app", include_in_schema=False)
def app_page():
    from fastapi.responses import FileResponse

    return FileResponse(_static_dir() / "app.html")
