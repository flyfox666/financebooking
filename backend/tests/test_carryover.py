from datetime import date
from decimal import Decimal

import pytest

from app.ledger import close_service
from app.ledger.exceptions import VoucherError


def test_generate_and_post_carryover(
    db_session, mock_month, book, mama_user, auditor_user, post_flow, get_posted_nets
):
    drafts = close_service.generate_carryover(
        db_session, book_id=book.id, period="2026-08", operator_id=mama_user.id
    )
    assert [v.carryover_type for v in drafts] == ["rd", "pnl"]
    rd, pnl = drafts
    assert rd.status == "draft"
    assert rd.voucher_date == date(2026, 8, 31)
    assert rd.attachment_count == 0
    assert [(l.account_code, l.debit, l.credit) for l in rd.lines] == [
        ("5602.06", Decimal("30000.00"), Decimal("0.00")),
        ("4301.01", Decimal("0.00"), Decimal("30000.00")),
    ]

    debit_map = {l.account_code: l.debit for l in pnl.lines if l.debit != 0}
    credit_map = {l.account_code: l.credit for l in pnl.lines if l.credit != 0}
    assert debit_map == {
        "5001": Decimal("27227.72"),
        "3103": Decimal("73361.33"),
    }
    assert credit_map == {
        "5401": Decimal("3200.00"),
        "5602.01": Decimal("3758.00"),
        "5602.02": Decimal("1350.00"),
        "5602.03": Decimal("2000.00"),
        "5602.04": Decimal("24600.00"),
        "5602.05": Decimal("333.33"),
        "5602.06": Decimal("30000.00"),
        "5602.07": Decimal("8000.00"),
        "5603": Decimal("120.00"),
        "3103": Decimal("27227.72"),
    }
    assert pnl.total_debit == Decimal("100589.05")

    with pytest.raises(VoucherError):
        close_service.generate_carryover(
            db_session, book_id=book.id, period="2026-08", operator_id=mama_user.id
        )

    for draft in drafts:
        post_flow(draft, mama_user, auditor_user)

    nets = get_posted_nets(book.id)
    assert "5001" not in nets
    assert "5401" not in nets
    assert "4301.01" not in nets
    assert "5602.06" not in nets
    assert nets["3103"] == Decimal("46133.61")
    assert nets["2221"] == Decimal("-722.28")


def test_carryover_requires_activity(db_session, book, mama_user):
    result = close_service.generate_carryover(
        db_session, book_id=book.id, period="2026-08", operator_id=mama_user.id
    )
    assert result == []
