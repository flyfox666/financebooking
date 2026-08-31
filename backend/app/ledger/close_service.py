import calendar
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ledger import voucher_service
from app.ledger.exceptions import VoucherError
from app.models.account import Account
from app.models.voucher import Voucher, VoucherLine

RD_PARENT = "4301"
RD_EXPENSE_NAME_KEYWORD = "费用化"
RD_TARGET_PARENT = "5602"
RD_TARGET_NAME_KEYWORD = "研究费用"
PROFIT_CODE = "3103"

POSTED_STATUSES = ("posted", "voided")


def _posted_sums(db: Session, book_id: int, period: str) -> dict[str, list[Decimal]]:
    rows = db.execute(
        select(VoucherLine.account_code, VoucherLine.debit, VoucherLine.credit)
        .join(Voucher, VoucherLine.voucher_id == Voucher.id)
        .where(
            Voucher.book_id == book_id,
            Voucher.period == period,
            Voucher.status.in_(POSTED_STATUSES),
        )
    ).all()
    sums: dict[str, list[Decimal]] = {}
    for code, debit, credit in rows:
        d, c = sums.get(code, [Decimal("0"), Decimal("0")])
        sums[code] = [d + Decimal(str(debit)), c + Decimal(str(credit))]
    return sums


def _find_rd_accounts(db: Session, book_id: int):
    rd_children = db.scalars(
        select(Account)
        .where(Account.book_id == book_id, Account.parent_code == RD_PARENT)
        .order_by(Account.code)
    ).all()
    rd_expense = next((a for a in rd_children if RD_EXPENSE_NAME_KEYWORD in a.name), None)
    target_children = db.scalars(
        select(Account)
        .where(Account.book_id == book_id, Account.parent_code == RD_TARGET_PARENT)
        .order_by(Account.code)
    ).all()
    target = next((a for a in target_children if RD_TARGET_NAME_KEYWORD in a.name), None)
    return rd_expense, target


def _carryover_exists(db: Session, book_id: int, period: str, carryover_type: str) -> bool:
    found = db.scalar(
        select(Voucher.id).where(
            Voucher.book_id == book_id,
            Voucher.period == period,
            Voucher.carryover_type == carryover_type,
            Voucher.status != "voided",
        )
    )
    return found is not None


def _period_end_date(period: str) -> date:
    year, month = int(period[:4]), int(period[5:7])
    return date(year, month, calendar.monthrange(year, month)[1])


def _line(summary: str, account_code: str, *, debit: Decimal = Decimal("0"), credit: Decimal = Decimal("0")) -> dict:
    return {"summary": summary, "account_code": account_code, "debit": debit, "credit": credit}


def generate_carryover(db: Session, *, book_id: int, period: str, operator_id: int) -> list[Voucher]:
    created: list[Voucher] = []
    sums = _posted_sums(db, book_id, period)

    rd_lines: list[dict] = []
    rd_net = Decimal("0")
    rd_expense, rd_target = _find_rd_accounts(db, book_id)
    if rd_expense is not None:
        d, c = sums.get(rd_expense.code, [Decimal("0"), Decimal("0")])
        rd_net = d - c
        if rd_net != 0:
            if rd_target is None:
                raise VoucherError("管理费用下未找到“研究费用”明细科目，请先增设后再结转研发支出")
            if not rd_target.is_leaf or not rd_target.is_active:
                raise VoucherError("研究费用科目必须是启用的明细科目")
            amount = abs(rd_net)
            if rd_net > 0:
                rd_lines = [
                    _line("结转研发支出—费用化支出", rd_target.code, debit=amount),
                    _line("结转研发支出—费用化支出", rd_expense.code, credit=amount),
                ]
            else:
                rd_lines = [
                    _line("冲回研发支出—费用化支出结转", rd_expense.code, debit=amount),
                    _line("冲回研发支出—费用化支出结转", rd_target.code, credit=amount),
                ]
    if rd_lines:
        if _carryover_exists(db, book_id, period, "rd"):
            raise VoucherError("本期已存在研发支出结转凭证，请先处理原凭证")
        voucher = voucher_service.create_voucher(
            db,
            book_id=book_id,
            voucher_date=_period_end_date(period),
            attachment_count=0,
            source="carryover",
            lines=rd_lines,
            operator_id=operator_id,
        )
        voucher.carryover_type = "rd"
        db.commit()
        db.refresh(voucher)
        created.append(voucher)
        target_entry = sums.setdefault(rd_target.code, [Decimal("0"), Decimal("0")])
        if rd_net > 0:
            target_entry[0] += rd_net
        else:
            target_entry[1] += abs(rd_net)

    pnl_accounts = db.scalars(
        select(Account)
        .where(
            Account.book_id == book_id,
            Account.category == "pnl",
            Account.is_leaf.is_(True),
        )
        .order_by(Account.code)
    ).all()
    income_total = Decimal("0")
    expense_total = Decimal("0")
    debit_lines: list[dict] = []
    credit_lines: list[dict] = []
    for account in pnl_accounts:
        d, c = sums.get(account.code, [Decimal("0"), Decimal("0")])
        if d == 0 and c == 0:
            continue
        normal = (d - c) * account.direction
        if normal == 0:
            continue
        amount = abs(normal)
        if account.direction == -1:
            income_total += normal
            if normal > 0:
                debit_lines.append(_line("结转损益", account.code, debit=amount))
            else:
                credit_lines.append(_line("结转损益", account.code, credit=amount))
        else:
            expense_total += normal
            if normal > 0:
                credit_lines.append(_line("结转损益", account.code, credit=amount))
            else:
                debit_lines.append(_line("结转损益", account.code, debit=amount))
    if debit_lines or credit_lines:
        if _carryover_exists(db, book_id, period, "pnl"):
            raise VoucherError("本期已存在损益结转凭证，请先处理原凭证")
        if income_total > 0:
            credit_lines.append(_line("结转本月收入至本年利润", PROFIT_CODE, credit=income_total))
        elif income_total < 0:
            debit_lines.append(_line("结转本月收入至本年利润", PROFIT_CODE, debit=-income_total))
        if expense_total > 0:
            debit_lines.append(_line("结转本月费用至本年利润", PROFIT_CODE, debit=expense_total))
        elif expense_total < 0:
            credit_lines.append(_line("结转本月费用至本年利润", PROFIT_CODE, credit=-expense_total))
        voucher = voucher_service.create_voucher(
            db,
            book_id=book_id,
            voucher_date=_period_end_date(period),
            attachment_count=0,
            source="carryover",
            lines=debit_lines + credit_lines,
            operator_id=operator_id,
        )
        voucher.carryover_type = "pnl"
        db.commit()
        db.refresh(voucher)
        created.append(voucher)
    return created
