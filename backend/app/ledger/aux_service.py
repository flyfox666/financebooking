"""辅助核算（往来单位维度）：往来档案 + 科目辅助属性 + 辅助余额聚合。"""

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ledger.balances import ZERO, fmt_amount, opening_nets
from app.ledger.exceptions import AccountError, BookError, LedgerError
from app.models.account import Account
from app.models.contact import Contact
from app.models.voucher import Voucher, VoucherLine

POSTED_STATUSES = ("posted", "voided")


def create_contact(db: Session, *, book_id: int, name: str, ctype: str, tax_no: str = "") -> Contact:
    name = (name or "").strip()
    if not (1 <= len(name) <= 128):
        raise BookError("往来单位名称长度须在 1~128 字之间")
    if ctype not in ("customer", "supplier"):
        raise BookError("往来类型须为 customer 或 supplier")
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
        if ctype not in ("customer", "supplier"):
            raise BookError("往来类型须为 customer 或 supplier")
        contact.ctype = ctype
    if is_active is not None:
        contact.is_active = is_active
    db.commit()
    db.refresh(contact)
    return contact


def set_aux_types(db: Session, *, book_id: int, code: str, aux_types: list[str]) -> Account:
    account = db.scalar(
        select(Account).where(Account.book_id == book_id, Account.code == code)
    )
    if account is None:
        raise AccountError(f"科目 {code} 不存在")
    for item in aux_types:
        if item not in ("contact",):
            raise AccountError("当前仅支持往来单位辅助核算（contact）")
    account.aux_types = ",".join(sorted(set(aux_types)))
    db.commit()
    db.refresh(account)
    return account


def contact_is_required(db: Session, account: Account) -> bool:
    return "contact" in (account.aux_types or "")


def aux_trial_balance(db: Session, *, book_id: int, period: str, account_code: str) -> dict:
    account = db.scalar(
        select(Account).where(Account.book_id == book_id, Account.code == account_code)
    )
    if account is None:
        raise AccountError(f"科目 {account_code} 不存在")
    if not contact_is_required(db, account):
        raise AccountError(f"科目 {account_code} 未启用辅助核算")

    opening = opening_nets(db, book_id, before_period=period)
    opening_by_contact: dict[int | None, Decimal] = {}

    rows = db.execute(
        select(VoucherLine.contact_id, VoucherLine.debit, VoucherLine.credit)
        .join(Voucher, VoucherLine.voucher_id == Voucher.id)
        .where(
            Voucher.book_id == book_id,
            Voucher.period <= period,
            Voucher.status.in_(POSTED_STATUSES),
            VoucherLine.account_code == account_code,
        )
    ).all()

    totals: dict[int | None, list[Decimal]] = {}
    for contact_id, debit, credit in rows:
        entry = totals.setdefault(contact_id, [ZERO, ZERO])
        entry[0] += Decimal(str(debit))
        entry[1] += Decimal(str(credit))

    contacts = {c.id: c for c in db.scalars(select(Contact).where(Contact.book_id == book_id))}
    result = []
    for contact_id, (period_debit, period_credit) in sorted(totals.items(), key=lambda item: str(item[0])):
        net = period_debit - period_credit
        contact = contacts.get(contact_id)
        closing_debit, closing_credit = (net, ZERO) if net > 0 else ((ZERO, -net) if net < 0 else (ZERO, ZERO))
        result.append(
            {
                "contact_id": contact_id,
                "contact_name": contact.name if contact else "（未指定）",
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
        "opening_net": fmt_amount(opening.get(account_code, ZERO)),
        "rows": result,
    }
