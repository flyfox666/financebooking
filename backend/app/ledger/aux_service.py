"""辅助核算（往来单位维度）：往来档案 + 科目辅助配置 + 辅助余额聚合。

aux_types 格式（逗号分隔多值）：
  ""                                未启用辅助核算
  "contact:customer"                启用往来核算，仅允许客户
  "contact:employee,contact:other"  启用往来核算，允许员工与其他往来
旧格式 "contact"（无类型后缀）由 normalize 辅助函数兼容解析为客户+供应商。
"""

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ledger.balances import ZERO, fmt_amount, opening_nets
from app.ledger.exceptions import AccountError, BookError, LedgerError
from app.models.account import Account
from app.models.contact import Contact
from app.models.voucher import Voucher, VoucherLine

POSTED_STATUSES = ("posted", "voided")
CONTACT_TYPES = ("customer", "supplier", "employee", "other")
LEGACY_CONTACT = "contact"
DEFAULT_AUX_CONFIG: dict[str, list[str]] = {
    "1121": ["contact:customer"],
    "1122": ["contact:customer"],
    "1123": ["contact:supplier"],
    "1221": ["contact:employee", "contact:other"],
    "2202": ["contact:supplier"],
    "2203": ["contact:customer"],
    "2241": ["contact:employee", "contact:other"],
}


def normalize_aux_types(raw: str | None) -> list[str]:
    items = [x.strip() for x in (raw or "").split(",") if x.strip()]
    if LEGACY_CONTACT in items:
        items = [x for x in items if x != LEGACY_CONTACT] + ["contact:customer", "contact:supplier"]
    return sorted(set(items))


def allowed_contact_types(raw: str | None) -> list[str]:
    return [x.split(":", 1)[1] for x in normalize_aux_types(raw) if x.startswith("contact:")]


def seed_aux_defaults(db: Session, book_id: int) -> None:
    for code, config in DEFAULT_AUX_CONFIG.items():
        account = db.scalar(
            select(Account).where(Account.book_id == book_id, Account.code == code)
        )
        if account is not None and not (account.aux_types or "").strip():
            account.aux_types = ",".join(config)
    db.commit()


def create_contact(db: Session, *, book_id: int, name: str, ctype: str, tax_no: str = "") -> Contact:
    name = (name or "").strip()
    if not (1 <= len(name) <= 128):
        raise BookError("往来单位名称长度须在 1~128 字之间")
    if ctype not in CONTACT_TYPES:
        raise BookError("往来类型须为 customer/supplier/employee/other")
    exists = db.scalar(
        select(Contact).where(Contact.book_id == book_id, Contact.name == name)
    )
    if exists:
        raise BookError(f"往来单位 {name} 已存在")
    contact = Contact(book_id=book_id, name=name, tax_no=(tax_no or "").strip(), ctype=ctype)
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact


def list_contacts(db: Session, book_id: int, ctype: str | None = None) -> list[Contact]:
    stmt = select(Contact).where(Contact.book_id == book_id)
    if ctype:
        stmt = stmt.where(Contact.ctype == ctype)
    return list(db.scalars(stmt.order_by(Contact.id)))


def update_contact(db: Session, contact_id: int, *, name: str | None, tax_no: str | None, ctype: str | None, is_active: bool | None) -> Contact:
    contact = db.get(Contact, contact_id)
    if contact is None:
        raise BookError("往来单位不存在")
    if name is not None:
        name = name.strip()
        if not (1 <= len(name) <= 128):
            raise BookError("往来单位名称长度须在 1~128 字之间")
        contact.name = name
    if tax_no is not None:
        contact.tax_no = tax_no.strip()
    if ctype is not None:
        if ctype not in CONTACT_TYPES:
            raise BookError("往来类型须为 customer/supplier/employee/other")
        used = contact_used_by(db, contact.book_id, contact.id)
        if used and ctype != contact.ctype:
            raise BookError(f"该往来单位已被科目 {', '.join(used)} 的凭证使用，不能修改类型")
        contact.ctype = ctype
    if is_active is not None:
        contact.is_active = is_active
    db.commit()
    db.refresh(contact)
    return contact


def contact_used_by(db: Session, book_id: int, contact_id: int) -> list[str]:
    codes = db.scalars(
        select(VoucherLine.account_code)
        .join(Voucher, VoucherLine.voucher_id == Voucher.id)
        .where(
            Voucher.book_id == book_id,
            VoucherLine.contact_id == contact_id,
        )
        .distinct()
    ).all()
    return sorted(set(codes))


def contact_used_by_labels(db: Session, book_id: int, contact_id: int) -> list[str]:
    labels = []
    for code in contact_used_by(db, book_id, contact_id):
        account = db.scalar(
            select(Account).where(Account.book_id == book_id, Account.code == code[:4])
        )
        labels.append(f"{account.code} {account.name}" if account else code)
    return sorted(set(labels))


def account_has_usage(db: Session, book_id: int, code: str) -> bool:
    exists = db.scalar(
        select(VoucherLine.id)
        .join(Voucher, VoucherLine.voucher_id == Voucher.id)
        .where(
            Voucher.book_id == book_id,
            VoucherLine.account_code.like(code + "%"),
        )
        .limit(1)
    )
    return exists is not None


def set_aux_types(db: Session, *, book_id: int, code: str, aux_types: list[str]) -> tuple[Account, bool]:
    account = db.scalar(
        select(Account).where(Account.book_id == book_id, Account.code == code)
    )
    if account is None:
        raise AccountError(f"科目 {code} 不存在")
    for item in aux_types:
        if not item.startswith("contact:") or item.split(":", 1)[1] not in CONTACT_TYPES:
            raise AccountError("辅助类型格式应为 contact:customer/supplier/employee/other")
    new_value = ",".join(sorted(set(aux_types)))
    changed = (account.aux_types or "") != new_value
    warned = False
    if changed and account_has_usage(db, book_id, code):
        warned = True
    account.aux_types = new_value
    db.commit()
    db.refresh(account)
    return account, warned


def aux_trial_balance(
    db: Session, *, book_id: int, period: str, account_code: str, ctype: str | None = None
) -> dict:
    account = db.scalar(
        select(Account).where(Account.book_id == book_id, Account.code == account_code)
    )
    if account is None:
        raise AccountError(f"科目 {account_code} 不存在")
    allowed = allowed_contact_types(account.aux_types)
    if not allowed:
        raise AccountError(f"科目 {account_code} 未启用往来辅助核算")

    rows = db.execute(
        select(VoucherLine.contact_id, VoucherLine.debit, VoucherLine.credit)
        .join(Voucher, VoucherLine.voucher_id == Voucher.id)
        .where(
            Voucher.book_id == book_id,
            Voucher.period <= period,
            Voucher.status.in_(POSTED_STATUSES),
            VoucherLine.account_code.like(account_code + "%"),
        )
    ).all()

    contacts = {c.id: c for c in db.scalars(select(Contact).where(Contact.book_id == book_id))}
    totals: dict[int | None, list[Decimal]] = {}
    for contact_id, debit, credit in rows:
        contact = contacts.get(contact_id)
        if ctype and (contact is None or contact.ctype != ctype):
            continue
        entry = totals.setdefault(contact_id, [ZERO, ZERO])
        entry[0] += Decimal(str(debit))
        entry[1] += Decimal(str(credit))

    result = []
    for contact_id, (period_debit, period_credit) in sorted(totals.items(), key=lambda item: str(item[0])):
        net = period_debit - period_credit
        contact = contacts.get(contact_id)
        closing_debit, closing_credit = (net, ZERO) if net > 0 else ((ZERO, -net) if net < 0 else (ZERO, ZERO))
        result.append(
            {
                "contact_id": contact_id,
                "contact_name": contact.name if contact else "（未指定）",
                "contact_type": contact.ctype if contact else "",
                "period_debit": fmt_amount(period_debit),
                "period_credit": fmt_amount(period_credit),
                "closing_debit": fmt_amount(closing_debit),
                "closing_credit": fmt_amount(closing_credit),
            }
        )
    return {
        "account_code": account_code,
        "account_name": account.name,
        "period": period,
        "allowed_types": allowed,
        "rows": result,
    }
