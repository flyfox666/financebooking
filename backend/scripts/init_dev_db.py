import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from sqlalchemy import func, select

from app.core.database import Base, SessionLocal, engine
from app.core.security import hash_password
from app.ledger import book_service
from app.ledger.account_service import seed_accounts
from app.models.account import Account
from app.models.book import Book
from app.models.user import User


def main() -> None:
    parser = argparse.ArgumentParser(
        description="初始化开发数据库：建表 + 管理员 + 账套并预置 66 个一级科目"
    )
    parser.add_argument("--admin-username", default="admin")
    parser.add_argument("--admin-password", default="admin123456")
    parser.add_argument("--book-name", default="示例科技有限公司")
    parser.add_argument("--start-period", default="2026-08")
    args = parser.parse_args()

    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        user_count = db.scalar(select(func.count()).select_from(User))
        if not user_count:
            db.add(
                User(
                    username=args.admin_username,
                    password_hash=hash_password(args.admin_password),
                    display_name="管理员",
                    role="admin",
                )
            )
            db.commit()
            print(f"已创建管理员账号：{args.admin_username}")
        else:
            print("管理员账号已存在，跳过")

        book_count = db.scalar(select(func.count()).select_from(Book))
        if not book_count:
            book = book_service.create_book(
                db, name=args.book_name, start_period=args.start_period
            )
            print(f"已创建账套：{book.name}（id={book.id}，启用期间 {book.start_period}）")
        else:
            book = db.scalar(select(Book).order_by(Book.id))
            print(f"账套已存在：{book.name}（id={book.id}），跳过创建")

        created = seed_accounts(db, book.id)
        total = db.scalar(
            select(func.count()).select_from(Account).where(Account.book_id == book.id)
        )
        active = db.scalar(
            select(func.count())
            .select_from(Account)
            .where(Account.book_id == book.id, Account.is_active.is_(True))
        )
        print(f"本次新增科目 {created} 个；账套科目总数 {total}（默认启用 {active}，66 个一级科目已预置）")
        print("初始化完成。")


if __name__ == "__main__":
    main()
