"""一次性回填：为存量凭证的现金行按「对方最大行科目映射」补 cf_item。"""
from collections import defaultdict

from sqlalchemy import select

from app.core.database import SessionLocal
from app.ledger.cashflow import CASH_ACCOUNTS, INFLOW_MAP, OUTFLOW_MAP
from app.models.voucher import Voucher, VoucherLine

with SessionLocal() as db:
    rows = db.execute(
        select(Voucher, VoucherLine)
        .join(VoucherLine, VoucherLine.voucher_id == Voucher.id)
        .order_by(Voucher.voucher_date, Voucher.voucher_no, VoucherLine.line_no)
    ).all()

    grouped: dict[int, list] = defaultdict(list)
    for voucher, line in rows:
        grouped[voucher.id].append(line)

    patched = 0
    for lines in grouped.values():
        cash_lines = [l for l in lines if l.account_code[:4] in CASH_ACCOUNTS]
        if not cash_lines or any(l.cf_item for l in cash_lines):
            continue
        others = [
            (l.account_code, l.debit - l.credit)
            for l in lines
            if l.account_code[:4] not in CASH_ACCOUNTS
        ]
        if not others:
            continue
        main_code = max(others, key=lambda x: abs(x[1]))[0]
        for l in cash_lines:
            amount = l.debit - l.credit
            if amount > 0:
                l.cf_item = INFLOW_MAP.get(main_code, "other_in")
            elif amount < 0:
                l.cf_item = OUTFLOW_MAP.get(main_code, "other_out")
            patched += 1
    db.commit()
    print(f"backfilled {patched} cash lines")
