import re
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.ledger.account_service import get_account, seed_accounts
from app.ledger.balances import fmt_amount
from app.ledger.exceptions import BookError
from app.ledger.report_templates import seed_report_templates
from app.models.book import Book, UserBook
from app.models.report import OpeningBalance
from app.models.user import User
from app.models.voucher import Voucher

TWO_PLACES = Decimal("0.01")
PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
TAXPAYER_TYPES = {"small_scale", "general"}
ENTITY_TYPES = {"company", "individual", "partnership"}
POSTED_STATUSES = ("posted", "voided")
BOOK_ROLES = ("admin", "bookkeeper", "auditor")


def add_member(db: Session, *, user_id: int, book_id: int, role: str = "bookkeeper") -> UserBook:
    """挂用户为账套成员（幂等：已存在则更新角色）。"""
    if role not in BOOK_ROLES:
        raise BookError("账套角色不合法")
    membership = db.scalar(
        select(UserBook).where(UserBook.user_id == user_id, UserBook.book_id == book_id)
    )
    if membership is None:
        membership = UserBook(user_id=user_id, book_id=book_id, role=role)
        db.add(membership)
    else:
        membership.role = role
    db.commit()
    return membership


def user_can_access(db: Session, user: User, book_id: int) -> bool:
    """账套访问判定：全局 admin 天然可见全部；其他用户须为账套成员。"""
    if user.role == "admin":
        return True
    found = db.scalar(
        select(UserBook.id).where(UserBook.user_id == user.id, UserBook.book_id == book_id)
    )
    return found is not None


def visible_books(db: Session, user: User) -> list[Book]:
    """当前用户可见账套：全局 admin 全部；其他用户为被授权成员的账套。"""
    if user.role == "admin":
        return list(db.scalars(select(Book).order_by(Book.id)).all())
    rows = db.execute(
        select(Book).join(UserBook, UserBook.book_id == Book.id).where(UserBook.user_id == user.id).order_by(Book.id)
    ).scalars().all()
    return list(rows)


def book_members(db: Session, book_id: int) -> list[dict]:
    """账套成员列表（含用户名/姓名/全局角色，供授权弹窗展示）。"""
    rows = db.execute(
        select(UserBook, User)
        .join(User, User.id == UserBook.user_id)
        .where(UserBook.book_id == book_id)
        .order_by(UserBook.id)
    ).all()
    return [
        {
            "user_id": membership.user_id,
            "username": user.username,
            "display_name": user.display_name,
            "global_role": user.role,
            "book_role": membership.role,
        }
        for membership, user in rows
    ]


def set_book_members(db: Session, *, book_id: int, members: list[dict]) -> int:
    """整体替换账套成员（全量设置，授权弹窗保存用）。

    校验：
    - role 合法；
    - 替换后账套仍「可管」：存在全局 admin 用户，或至少一个 book_role=admin 的成员。
    """
    from app.models.user import User

    if db.get(Book, book_id) is None:
        raise BookError("账套不存在")
    cleaned: list[tuple[int, str]] = []
    seen: set[int] = set()
    for item in members:
        user_id = int(item.get("user_id") or 0)
        role = str(item.get("role") or "bookkeeper")
        if role not in BOOK_ROLES:
            raise BookError("账套角色不合法")
        if db.get(User, user_id) is None:
            raise BookError(f"用户 {user_id} 不存在")
        if user_id in seen:
            continue
        seen.add(user_id)
        cleaned.append((user_id, role))

    # 最低可管性：替换后必须有全局 admin，或成员里有 admin 角色用户
    has_global_admin = db.scalar(
        select(func.count()).select_from(User).where(User.role == "admin", User.is_active.is_(True))
    )
    has_member_admin = any(role == "admin" for _, role in cleaned)
    if not has_global_admin and not has_member_admin:
        raise BookError("账套至少需要一个可管理的管理员（全局 admin 或账套 admin 成员）")

    db.execute(delete(UserBook).where(UserBook.book_id == book_id))
    for user_id, role in cleaned:
        db.add(UserBook(user_id=user_id, book_id=book_id, role=role))
    db.commit()
    return len(cleaned)


def user_books(db: Session, user_id: int) -> list[dict]:
    """用户被授权的账套列表（用户视角，授权弹窗展示）。"""
    rows = db.execute(
        select(Book, UserBook.role)
        .join(UserBook, UserBook.book_id == Book.id)
        .where(UserBook.user_id == user_id)
        .order_by(Book.id)
    ).all()
    return [{"book_id": b.id, "name": b.name, "role": role} for b, role in rows]


def set_user_books(db: Session, *, user_id: int, books: list[dict]) -> int:
    """整体设置用户的账套授权（用户视角，授权弹窗保存用）。

    校验 role 合法 + 目标用户存在；对每个账套做最低可管性检查
    （若该用户是某账套唯一的 admin 成员且被移除，需存在全局 admin 或其他 admin 成员兜底）。
    """
    from app.models.user import User

    user = db.get(User, user_id)
    if user is None:
        raise BookError("用户不存在")
    cleaned: list[tuple[int, str]] = []
    seen: set[int] = set()
    for item in books:
        book_id = int(item.get("book_id") or 0)
        role = str(item.get("role") or "bookkeeper")
        if role not in BOOK_ROLES:
            raise BookError("账套角色不合法")
        if db.get(Book, book_id) is None:
            raise BookError(f"账套 {book_id} 不存在")
        if book_id in seen:
            continue
        seen.add(book_id)
        cleaned.append((book_id, role))

    if user.role == "admin":
        # 全局 admin 不依赖成员表，直接全量替换
        db.execute(delete(UserBook).where(UserBook.user_id == user_id))
        for book_id, role in cleaned:
            db.add(UserBook(user_id=user_id, book_id=book_id, role=role))
        db.commit()
        return len(cleaned)

    # 非 admin 用户被移出的账套：检查是否失去唯一 admin 成员
    current = {ub.book_id: ub.role for ub in db.scalars(select(UserBook).where(UserBook.user_id == user_id))}
    removed = set(current) - seen
    for book_id in removed:
        others = db.scalars(
            select(UserBook).where(UserBook.book_id == book_id, UserBook.user_id != user_id)
        ).all()
        has_member_admin = any(m.role == "admin" for m in others)
        has_global_admin = db.scalar(
            select(func.count()).select_from(User).where(User.role == "admin", User.is_active.is_(True))
        )
        if not has_member_admin and not has_global_admin:
            raise BookError("该用户是此账套唯一的管理员成员，请先授权其他管理员")

    db.execute(delete(UserBook).where(UserBook.user_id == user_id))
    for book_id, role in cleaned:
        db.add(UserBook(user_id=user_id, book_id=book_id, role=role))
    db.commit()
    return len(cleaned)


def create_book(
    db: Session,
    *,
    name: str,
    tax_no: str = "",
    taxpayer_type: str = "small_scale",
    entity_type: str = "company",
    start_period: str,
) -> Book:
    name = (name or "").strip()
    if not (2 <= len(name) <= 128):
        raise BookError("企业名称长度须在 2~128 字之间")
    if taxpayer_type not in TAXPAYER_TYPES:
        raise BookError("纳税人身份不合法")
    if entity_type not in ENTITY_TYPES:
        raise BookError("主体类型不合法")
    tax_no = (tax_no or "").strip()
    if tax_no and not re.fullmatch(r"[0-9A-Z]{18}", tax_no):
        raise BookError("统一社会信用代码须为 18 位数字或大写字母")
    if not PERIOD_RE.match(start_period or ""):
        raise BookError("启用期间格式应为 YYYY-MM")
    book = Book(
        name=name,
        tax_no=tax_no,
        taxpayer_type=taxpayer_type,
        entity_type=entity_type,
        start_period=start_period,
    )
    db.add(book)
    db.flush()
    seed_accounts(db, book.id)
    seed_report_templates(db, book.id)
    from app.ledger.aux_service import seed_aux_defaults
    from app.ledger.tax.params import seed_tax_params

    seed_aux_defaults(db, book.id)
    seed_tax_params(db, book.id)
    db.refresh(book)
    return book


def set_opening_balances(db: Session, *, book_id: int, items: list[dict]) -> dict:
    book = db.get(Book, book_id)
    if book is None:
        raise BookError("账套不存在")
    posted = db.scalar(
        select(func.count())
        .select_from(Voucher)
        .where(Voucher.book_id == book_id, Voucher.status.in_(POSTED_STATUSES))
    )
    if posted:
        raise BookError("账套已存在过账凭证，不能修改期初余额")
    total_debit = Decimal("0")
    total_credit = Decimal("0")
    cleaned: list[tuple[str, Decimal, Decimal]] = []
    for item in items:
        code = str(item.get("account_code") or "").strip()
        account = get_account(db, book_id, code)
        if account is None:
            raise BookError(f"科目 {code} 不存在")
        if not account.is_leaf:
            raise BookError(f"科目 {code} 不是末级科目，期初余额只能录入末级科目")
        try:
            debit = Decimal(str(item.get("debit") or 0)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            credit = Decimal(str(item.get("credit") or 0)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        except Exception:
            raise BookError("期初金额格式不合法")
        if debit > 0 and credit > 0:
            raise BookError(f"科目 {code} 的期初借方与贷方不能同时有值")
        cleaned.append((code, debit, credit))
        total_debit += debit
        total_credit += credit
    if total_debit != total_credit:
        raise BookError(f"期初试算不平衡：借方合计 {total_debit}，贷方合计 {total_credit}")
    db.execute(delete(OpeningBalance).where(OpeningBalance.book_id == book_id))
    for code, debit, credit in cleaned:
        db.add(OpeningBalance(book_id=book_id, account_code=code, debit=debit, credit=credit))
    db.commit()
    return {
        "items": len(cleaned),
        "total_debit": fmt_amount(total_debit),
        "total_credit": fmt_amount(total_credit),
    }
