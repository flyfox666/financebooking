from __future__ import annotations

import json
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ledger.exceptions import BookError, VoucherError
from app.models.account import Account
from app.models.book import Book
from app.models.contact import Contact
from app.models.report import PeriodClose
from app.models.user import User
from app.models.voucher import Voucher, VoucherLine

TWO_PLACES = Decimal("0.01")
ALLOWED_SOURCES = {"manual", "ai", "carryover", "reverse"}


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _to_amount(value) -> Decimal:
    try:
        amount = _quantize(Decimal(str(value)))
        if not amount.is_finite() or abs(amount) >= Decimal('10000000000000000'):
            raise ValueError()
        return amount
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
        contact_id = raw.get("contact_id") or None
        aux_config = [x for x in (account.aux_types or "").split(",") if x]
        contact_required = bool(aux_config)
        allowed_types = [
            x.split(":", 1)[1] for x in aux_config if x.startswith("contact:")
        ]
        if strict_accounts:
            if not account.is_active:
                raise VoucherError(f"科目 {account_code} 已停用，不能在新凭证中使用")
            if not account.is_leaf:
                raise VoucherError(f"科目 {account_code} 存在下级明细，请使用明细科目")
        if contact_required:
            if not contact_id:
                raise VoucherError(f"第 {idx} 行科目 {account_code} 启用了往来辅助核算，必须选择往来单位")
            contact = db.get(Contact, int(contact_id))
            if contact is None or contact.book_id != book_id or not contact.is_active:
                raise VoucherError(f"第 {idx} 行往来单位不存在或已停用")
            if allowed_types and contact.ctype not in allowed_types:
                type_cn = {"customer": "客户", "supplier": "供应商", "employee": "员工", "other": "其他往来"}
                raise VoucherError(
                    f"第 {idx} 行科目 {account_code} 仅允许往来类型 "
                    f"{'、'.join(type_cn[t] for t in allowed_types)}，所选「{contact.name}」为{type_cn[contact.ctype]}"
                )
        elif contact_id:
            contact = db.get(Contact, int(contact_id))
            if contact is None or contact.book_id != book_id:
                raise VoucherError(f"第 {idx} 行往来单位不存在")
        prepared.append(
            {
                "line_no": idx,
                "summary": summary,
                "account_code": account_code,
                "debit": debit,
                "credit": credit,
                "contact_id": int(contact_id) if contact_id else None,
                "cf_item": None,
            }
        )
        total_debit += debit
    total_credit = sum((row["credit"] for row in prepared), Decimal("0"))
    if total_debit != total_credit:
        raise VoucherError(f"借贷不平衡：借方合计 {total_debit}，贷方合计 {total_credit}")
    if total_debit == 0:
        raise VoucherError("凭证合计金额不能为零")
    _apply_cf_items(raw_lines, prepared)
    return prepared, total_debit


def _apply_cf_items(raw_lines, prepared: list[dict]) -> None:
    """现金流量标注：现金类科目行合法 cf_item 保留并持久化；非现金行清除；
    现金行缺标或值非法时按「对方最大行科目映射」自动兜底填充（报表永不缺数）。"""
    from app.ledger.cashflow import CASH_ACCOUNTS, INFLOW_MAP, ITEM_LABELS, OUTFLOW_MAP

    others = [
        (row["account_code"], row["debit"] - row["credit"])
        for row in prepared
        if row["account_code"][:4] not in CASH_ACCOUNTS
    ]
    main_code = max(others, key=lambda x: abs(x[1]))[0] if others else ""

    for raw, row in zip(raw_lines, prepared):
        if row["account_code"][:4] not in CASH_ACCOUNTS:
            continue
        cf = str(raw.get("cf_item") or "").strip() if isinstance(raw, dict) else None
        valid = cf in ITEM_LABELS if cf else False
        if not valid:
            # 兜底：按对方最大行科目映射，方向看本行现金增减
            cash_amount = row["debit"] - row["credit"]
            if cash_amount > 0:
                cf = INFLOW_MAP.get(main_code, INFLOW_MAP.get(main_code[:4], "other_in"))
            elif cash_amount < 0:
                cf = OUTFLOW_MAP.get(main_code, OUTFLOW_MAP.get(main_code[:4], "other_out"))
        row["cf_item"] = cf or None


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
    commit: bool = True,
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
    if commit:
        db.commit()
    else:
        db.flush()
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
    operator_id: int | None = None,
) -> Voucher:
    voucher = _load_voucher(db, voucher_id)
    if voucher.status != "draft":
        raise VoucherError("只有草稿凭证可以修改")
    _ensure_period_open(db, voucher.book_id, voucher.period)
    if operator_id is not None:
        operator = db.get(User, operator_id)
        if operator is None or operator.role not in ("admin", "bookkeeper"):
            raise VoucherError("审核岗不能修改凭证")
        voucher.edited_by = operator_id
        editors = set(json.loads(voucher.editor_ids_json or '[]'))
        editors.add(operator_id)
        voucher.editor_ids_json = json.dumps(sorted(editors))
    if voucher_date is not None:
        new_date = _ensure_date(voucher_date)
        new_period = f"{new_date:%Y-%m}"
        if new_period < db.get(Book, voucher.book_id).start_period:
            raise VoucherError("凭证期间早于账套启用期间")
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
    _ensure_period_open(db, voucher.book_id, voucher.period)
    _ensure_originals(db, voucher)
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
    if operator.id in {voucher.created_by, voucher.edited_by, *json.loads(voucher.editor_ids_json or '[]')}:
        raise VoucherError("制单与审核不能为同一人")
    _ensure_period_open(db, voucher.book_id, voucher.period)
    _ensure_originals(db, voucher)
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
    _ensure_period_open(db, voucher.book_id, voucher.period)
    _ensure_originals(db, voucher)
    if voucher.reverses_voucher_id:
        claimed = db.execute(update(Voucher).where(
            Voucher.id == voucher.reverses_voucher_id,
            Voucher.status == 'posted', Voucher.voided_by_voucher_id.is_(None),
        ).values(status='voided', voided_by_voucher_id=voucher.id))
        if claimed.rowcount != 1:
            db.rollback()
            raise VoucherError("原凭证已被冲销或状态发生变化")
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
    if voucher.reverses_voucher_id:
        original = db.get(Voucher, voucher.reverses_voucher_id)
        if original is None or original.voided_by_voucher_id != voucher.id:
            raise VoucherError("冲销关联异常，不能反过账")
        original.status = 'posted'
        original.voided_by_voucher_id = None
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
    if db.scalar(select(Voucher.id).where(Voucher.reverses_voucher_id == voucher.id)):
        raise VoucherError("已存在该凭证的红字冲销，请先处理原红冲草稿")
    _ensure_period_open(db, voucher.book_id, voucher.period)
    prefix = f"冲销{voucher.word}字第{voucher.voucher_no:04d}号："
    lines = [
        {
            "summary": (prefix + ln.summary)[:200],
            "account_code": ln.account_code,
            "debit": -Decimal(str(ln.debit)),
            "credit": -Decimal(str(ln.credit)),
            "contact_id": ln.contact_id,
            "cf_item": ln.cf_item,
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
        commit=False,
    )
    red.reverses_voucher_id = voucher.id
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise VoucherError("已存在该凭证的红字冲销")
    db.refresh(red)
    return red


def _ensure_originals(db: Session, voucher: Voucher) -> None:
    if voucher.source != "manual":
        return
    from app.ledger.attachment_service import list_attachments, read_attachment_file
    originals = list_attachments(db, voucher.id)
    if not originals:
        raise VoucherError("手工凭证提交前必须上传至少一张原始单据")
    for original in originals:
        read_attachment_file(original)
