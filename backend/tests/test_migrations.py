from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.ledger import book_service
from app.models.account import Account

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _make_config(db_path: Path) -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def test_alembic_upgrade_creates_full_working_schema(tmp_path):
    db_file = tmp_path / "migrated.db"
    command.upgrade(_make_config(db_file), "head")

    engine = create_engine(f"sqlite:///{db_file}")
    session = sessionmaker(bind=engine)()
    try:
        book = book_service.create_book(
            session, name="迁移验收公司", start_period="2026-08"
        )
        accounts = session.scalar(
            select(func.count()).select_from(Account).where(Account.book_id == book.id)
        )
        assert accounts == 66
    finally:
        session.close()
        engine.dispose()


def test_alembic_upgrade_is_idempotent(tmp_path):
    db_file = tmp_path / "migrated.db"
    cfg = _make_config(db_file)
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")
