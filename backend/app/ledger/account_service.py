import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ledger.accounts_catalog import ACCOUNT_CATALOG
from app.ledger.exceptions import AccountError
from app.models.account import Account

SUFFIX_RE = re.compile(r"^[0-9A-Za-z]{1,4}$")


def get_account(db: Session, book_id: int, code: str) -> Account | None:
    return db.scalar(
        select(Account).where(Account.book_id == book_id, Account.code == code)
    )


def seed_accounts(db: Session, book_id: int) -> int:
    existing = set(db.scalars(select(Account.code).where(Account.book_id == book_id)))
    created = 0
    for code, name, category, direction, default_active in ACCOUNT_CATALOG:
        if code in existing:
            continue
        db.add(
            Account(
                book_id=book_id,
                code=code,
                name=name,
                category=category,
                direction=direction,
                parent_code=None,
                level=1,
                is_active=default_active,
                is_leaf=True,
                is_preset=True,
            )
        )
        created += 1
    db.commit()
    return created


def create_detail_account(
    db: Session, *, book_id: int, parent_code: str, code: str, name: str
) -> Account:
    parent = get_account(db, book_id, parent_code)
    if parent is None:
        raise AccountError(f"上级科目 {parent_code} 不存在")
    if parent.level >= 3:
        raise AccountError("科目最多三级，不能在三级明细下继续增设")
    prefix = parent.code + "."
    if not code.startswith(prefix):
        raise AccountError(f"明细编码必须以 {prefix} 开头")
    suffix = code[len(prefix):]
    if not SUFFIX_RE.fullmatch(suffix):
        raise AccountError("明细编码后缀须为 1~4 位字母或数字")
    if get_account(db, book_id, code) is not None:
        raise AccountError(f"科目编码 {code} 已存在")
    name = (name or "").strip()
    if not (1 <= len(name) <= 64):
        raise AccountError("科目名称长度须在 1~64 字之间")
    child = Account(
        book_id=book_id,
        code=code,
        name=name,
        category=parent.category,
        direction=parent.direction,
        parent_code=parent.code,
        level=parent.level + 1,
        is_active=True,
        is_leaf=True,
        is_preset=False,
    )
    parent.is_leaf = False
    db.add(child)
    db.commit()
    db.refresh(child)
    return child


def set_account_active(db: Session, *, book_id: int, code: str, is_active: bool) -> Account:
    account = get_account(db, book_id, code)
    if account is None:
        raise AccountError(f"科目 {code} 不存在")
    if not is_active:
        active_children = db.scalar(
            select(func.count())
            .select_from(Account)
            .where(
                Account.book_id == book_id,
                Account.parent_code == code,
                Account.is_active.is_(True),
            )
        )
        if active_children:
            raise AccountError("存在启用的下级科目，不能停用")
    account.is_active = is_active
    db.commit()
    db.refresh(account)
    return account


def get_account_tree(db: Session, *, book_id: int, only_active: bool = True) -> list[dict]:
    stmt = select(Account).where(Account.book_id == book_id).order_by(Account.code)
    accounts = [a for a in db.scalars(stmt) if (a.is_active or not only_active)]
    nodes: dict[str, dict] = {}
    roots: list[dict] = []
    for a in accounts:
        nodes[a.code] = {
            "id": a.id,
            "code": a.code,
            "name": a.name,
            "category": a.category,
            "direction": a.direction,
            "parent_code": a.parent_code,
            "level": a.level,
            "is_active": a.is_active,
            "is_leaf": a.is_leaf,
            "is_preset": a.is_preset,
            "aux_types": a.aux_types or "",
            "children": [],
        }
    for a in accounts:
        node = nodes[a.code]
        if a.parent_code and a.parent_code in nodes:
            nodes[a.parent_code]["children"].append(node)
        else:
            roots.append(node)
    return roots
