import re
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.ledger.account_service import get_account, seed_accounts
from app.ledger.balances import fmt_amount
from app.ledger.exceptions import BookError
from app.ledger.report_templates import seed_report_templates
from app.models.book import Book
from app.models.report import OpeningBalance
from app.models.voucher import Voucher

TWO_PLACES = Decimal("0.01")
PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
TAXPAYER_TYPES = {"small_scale", "general"}
ENTITY_TYPES = {"company", "individual", "partnership"}
POSTED_STATUSES = ("posted", "voided")


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
