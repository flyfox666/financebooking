from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.ledger import balances, close_service, voucher_service
from app.ledger.exceptions import VoucherError
from app.models.report import PeriodBalance

BALANCED = [
    {"summary": "补录一笔费用", "account_code": "1002", "debit": "10.00", "credit": "0"},
    {"summary": "补录一笔费用", "account_code": "5603", "debit": "0", "credit": "10.00"},
]


def test_close_writes_snapshot_and_locks(db_session, mock_month_with_carryover, book, mama_user):
    result = close_service.close_period(
        db_session, book_id=book.id, period="2026-08", operator_id=mama_user.id
    )
    assert result["closed"] is True
    assert result["snapshot_accounts"] == 73

    snapshots = db_session.scalars(
        select(PeriodBalance).where(
            PeriodBalance.book_id == book.id, PeriodBalance.period == "2026-08"
        )
    ).all()
    assert len(snapshots) == 73
    snapshot_map = {s.account_code: s for s in snapshots}
    report = balances.trial_balance(db_session, book_id=book.id, period="2026-08")
    for row in report["rows"]:
        snapshot = snapshot_map[row["account_code"]]
        assert Decimal(str(snapshot.closing_debit)) == Decimal(row["closing_debit"])
        assert Decimal(str(snapshot.closing_credit)) == Decimal(row["closing_credit"])

    with pytest.raises(VoucherError, match="已结账"):
        voucher_service.create_voucher(
            db_session,
            book_id=book.id,
            voucher_date="2026-08-20",
            lines=BALANCED,
            operator_id=mama_user.id,
            attachment_count=1,
        )
    posted = mock_month_with_carryover["vouchers"][0]
    with pytest.raises(VoucherError, match="已结账"):
        voucher_service.unpost_voucher(db_session, voucher_id=posted.id)
    with pytest.raises(VoucherError, match="已结账"):
        voucher_service.reverse_voucher(db_session, voucher_id=posted.id, operator=mama_user)
    with pytest.raises(VoucherError, match="已结账"):
        close_service.close_period(db_session, book_id=book.id, period="2026-08", operator_id=mama_user.id)


def test_close_blocks_unposted_vouchers(db_session, mock_month, book, mama_user):
    voucher_service.create_voucher(
        db_session,
        book_id=book.id,
        voucher_date="2026-08-20",
        lines=BALANCED,
        operator_id=mama_user.id,
        attachment_count=1,
    )
    with pytest.raises(VoucherError, match="未过账凭证"):
        close_service.close_period(db_session, book_id=book.id, period="2026-08", operator_id=mama_user.id)


def test_unclose_reopens_period(db_session, mock_month_with_carryover, book, mama_user):
    close_service.close_period(db_session, book_id=book.id, period="2026-08", operator_id=mama_user.id)
    close_service.unclose_period(db_session, book_id=book.id, period="2026-08")

    remaining = db_session.scalar(
        select(func.count()).select_from(PeriodBalance).where(PeriodBalance.book_id == book.id)
    )
    assert remaining == 0

    voucher = voucher_service.create_voucher(
        db_session,
        book_id=book.id,
        voucher_date="2026-08-20",
        lines=BALANCED,
        operator_id=mama_user.id,
        attachment_count=1,
    )
    assert voucher.status == "draft"

    with pytest.raises(VoucherError, match="未结账"):
        close_service.unclose_period(db_session, book_id=book.id, period="2026-08")
