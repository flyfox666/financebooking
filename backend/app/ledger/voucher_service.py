from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ledger.exceptions import BookError, VoucherError
from app.models.account import Account
from app.models.book import Book
from app.models.report import PeriodClose
from app.models.user import User
from app.models.voucher import Voucher, VoucherLine

TWO_PLACES = Decimal("0.01")
ALLOWED_SOURCES = {"manual", "ai", "carryover", "reverse"}


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _to_amount(value) -> Decimal:
    try:
        return _quantize(Decimal(str(value)))
    except Exception:
        raise VoucherError("金额格式不合法")


def _ensure_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise VoucherError("凭证日期格式应为 YYYY-MM-DD")


def _load_voucher(db: Session, voucher_id: int) -> Voucher:
    voucher = db.get(Voucher, voucher_id)
    if voucher is None:
        raise VoucherError("凭证不存在")
    return voucher


def _next_voucher_no(db: Session, book_id: int, period: str) -> int:
    current = db.scalar(
        select(func.max(Voucher.voucher_no)).where(
            Voucher.book_id == book_id, Voucher.period == period
        )
    )
    return (current or 0) + 1


def _ensure_period_open(db: Session, book_id: int, period: str) -> None:
    closed = db.scalar(
        select(PeriodClose.id).where(
            PeriodClose.book_id == book_id, PeriodClose.period == period
        )
    )
    if closed is not None:
        raise VoucherError(f"期间 {period} 已结账，请先反结账后再操作")


def _validate_lines(
    db: Session, book_id: int, raw_lines, *, strict_accounts: bool = True
) -> tuple[list[dict], Decimal]:
    if not raw_lines or len(raw_lines) < 2:
        raise VoucherError("凭证至少需要两条分录")
    prepared: list[dict] = []
    total_debit = Decimal("0")
    for idx, raw in enumerate(raw_lines, start=1):
        summary = str(raw.get("summary") or "").strip()
        if not summary:
            raise VoucherError(f"第 {idx} 行分录缺少摘要")
        if len(summary) > 200:
            raise VoucherError(f"第 {idx} 行摘要过长")
        account_code = str(raw.get("account_code") or "").strip()
        debit = _to_amount(raw.get("debit") or 0)
        credit = _to_amount(raw.get("credit") or 0)
        if debit != 0 and credit != 0:
            raise VoucherError(f"第 {idx} 行借方与贷方金额不能同时有值")
        if debit == 0 and credit == 0:
            raise VoucherError(f"第 {idx} 行借方与贷方金额不能同时为零")
        account = db.scalar(
            select(Account).where(Account.book_id == book_id, Account.code == account_code)
        )
        if account is None:
            raise VoucherError(f"第 {idx} 行科目 {account_code} 不存在")
        if strict_accounts:
            if not account.is_active:
                raise VoucherError(f"科目 {account_code} 已停用，不能在新凭证中使用")
            if not account.is_leaf:
                raise VoucherError(f"科目 {account_code} 存在下级明细，请使用明细科目")
        prepared.append(
            {
                "line_no": idx,
                "summary": summary,
                "account_code": account_code,
                "debit": debit,
                "credit": credit,
            }
        )
        total_debit += debit
    total_credit = sum((row["credit"] for row in prepared), Decimal("0"))
    if total_debit != total_credit:
        raise VoucherError(f"借贷不平衡：借方合计 {total_debit}，贷方合计 {total_credit}")
    if total_debit == 0:
        raise VoucherError("凭证合计金额不能为零")
    return prepared, total_debit


def create_voucher(
    db: Session,
    *,
    book_id: int,
    voucher_date,
    lines: list[dict],
    operator_id: int,
    attachment_count: int = 0,
    source: str = "manual",
    strict_accounts: bool = True,
) -> Voucher:
    if source not in ALLOWED_SOURCES:
        raise VoucherError("凭证来源不合法")
    book = db.get(Book, book_id)
    if book is None:
        raise BookError("账套不存在")
    voucher_date = _ensure_date(voucher_date)
    period = f"{voucher_date:%Y-%m}"
    if period < book.start_period:
        raise VoucherError(f"凭证期间早于账套启用期间 {book.start_period}")
    _ensure_period_open(db, book_id, period)
    if source == "manual" and attachment_count < 1:
        raise VoucherError("凭证必须至少附一张原始凭证")
    prepared, total = _validate_lines(db, book_id, lines, strict_accounts=strict_accounts)
    voucher = Voucher(
        book_id=book_id,
        word="记",
        voucher_no=_next_voucher_no(db, book_id, period),
        period=period,
        voucher_date=voucher_date,
        attachment_count=attachment_count,
        status="draft",
        source=source,
        total_debit=total,
        total_credit=total,
        created_by=operator_id,
    )
    voucher.lines = [VoucherLine(**row) for row in prepared]
    db.add(voucher)
    db.commit()
    db.refresh(voucher)
    return voucher


def get_voucher(db: Session, voucher_id: int) -> Voucher:
    return _load_voucher(db, voucher_id)


def list_vouchers(
    db: Session, *, book_id: int, period: str | None = None, status: str | None = None
) -> list[Voucher]:
    stmt = select(Voucher).where(Voucher.book_id == book_id)
    if period:
        stmt = stmt.where(Voucher.period == period)
    if status:
        stmt = stmt.where(Voucher.status == status)
    stmt = stmt.order_by(Voucher.period, Voucher.voucher_no)
    return list(db.scalars(stmt))


def update_voucher(
    db: Session,
    *,
    voucher_id: int,
    voucher_date=None,
    attachment_count: int | None = None,
    lines: list[dict] | None = None,
) -> Voucher:
    voucher = _load_voucher(db, voucher_id)
    if voucher.status != "draft":
        raise VoucherError("只有草稿凭证可以修改")
    if voucher_date is not None:
        new_date = _ensure_date(voucher_date)
        new_period = f"{new_date:%Y-%m}"
        if new_period != voucher.period:
            _ensure_period_open(db, voucher.book_id, new_period)
            new_no = _next_voucher_no(db, voucher.book_id, new_period)
            voucher.voucher_date = new_date
            voucher.period = new_period
            voucher.voucher_no = new_no
        else:
            voucher.voucher_date = new_date
    if attachment_count is not None:
        voucher.attachment_count = attachment_count
    if lines is not None:
        prepared, total = _validate_lines(db, voucher.book_id, lines)
        voucher.lines.clear()
        db.flush()
        voucher.lines = [VoucherLine(**row) for row in prepared]
        voucher.total_debit = total
        voucher.total_credit = total
    db.commit()
    db.refresh(voucher)
    return voucher


def delete_voucher(db: Session, *, voucher_id: int) -> None:
    voucher = _load_voucher(db, voucher_id)
    if voucher.status != "draft":
        raise VoucherError("只有草稿凭证可以删除")
    from app.ledger.attachment_service import purge_voucher_attachments

    purge_voucher_attachments(db, voucher)
    db.delete(voucher)
    db.commit()


def submit_voucher(db: Session, *, voucher_id: int, operator_id: int) -> Voucher:
    voucher = _load_voucher(db, voucher_id)
    if voucher.status != "draft":
        raise VoucherError("只有草稿凭证可以提交审核")
    voucher.status = "submitted"
    db.commit()
    db.refresh(voucher)
    return voucher


def reject_voucher(db: Session, *, voucher_id: int, operator: User) -> Voucher:
    voucher = _load_voucher(db, voucher_id)
    if voucher.status != "submitted":
        raise VoucherError("只有待审核凭证可以驳回")
    voucher.status = "draft"
    db.commit()
    db.refresh(voucher)
    return voucher


def audit_voucher(db: Session, *, voucher_id: int, operator: User) -> Voucher:
    voucher = _load_voucher(db, voucher_id)
    if voucher.status != "submitted":
        raise VoucherError("只有待审核凭证可以审核")
    if operator.id == voucher.created_by:
        raise VoucherError("制单与审核不能为同一人")
    voucher.status = "audited"
    voucher.audited_by = operator.id
    voucher.audited_at = datetime.now()
    db.commit()
    db.refresh(voucher)
    return voucher


def post_voucher(db: Session, *, voucher_id: int, operator: User) -> Voucher:
    voucher = _load_voucher(db, voucher_id)
    if voucher.status != "audited":
        raise VoucherError("只有已审核凭证可以过账")
    voucher.status = "posted"
    voucher.posted_by = operator.id
    voucher.posted_at = datetime.now()
    if voucher.reverses_voucher_id:
        original = db.get(Voucher, voucher.reverses_voucher_id)
        if original is not None and original.status == "posted":
            original.status = "voided"
            original.voided_by_voucher_id = voucher.id
    db.commit()
    db.refresh(voucher)
    return voucher


def unpost_voucher(db: Session, *, voucher_id: int) -> Voucher:
    voucher = _load_voucher(db, voucher_id)
    if voucher.status != "posted":
        raise VoucherError("只有已过账凭证可以反过账")
    if voucher.voided_by_voucher_id:
        raise VoucherError("凭证已被冲销，不能反过账")
    _ensure_period_open(db, voucher.book_id, voucher.period)
    voucher.status = "audited"
    voucher.posted_by = None
    voucher.posted_at = None
    db.commit()
    db.refresh(voucher)
    return voucher


def reverse_voucher(db: Session, *, voucher_id: int, operator: User) -> Voucher:
    voucher = _load_voucher(db, voucher_id)
    if voucher.status != "posted":
        raise VoucherError("只有已过账凭证可以红字冲销")
    if voucher.voided_by_voucher_id:
        raise VoucherError("凭证已被冲销，不能重复冲销")
    _ensure_period_open(db, voucher.book_id, voucher.period)
    prefix = f"冲销{voucher.word}字第{voucher.voucher_no:04d}号："
    lines = [
        {
            "summary": (prefix + ln.summary)[:200],
            "account_code": ln.account_code,
            "debit": -Decimal(str(ln.debit)),
            "credit": -Decimal(str(ln.credit)),
        }
        for ln in voucher.lines
    ]
    red = create_voucher(
        db,
        book_id=voucher.book_id,
        voucher_date=voucher.voucher_date,
        lines=lines,
        operator_id=operator.id,
        attachment_count=0,
        source="reverse",
        strict_accounts=False,
    )
    red.reverses_voucher_id = voucher.id
    db.commit()
    db.refresh(red)
    return red
