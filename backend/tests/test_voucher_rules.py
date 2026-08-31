from decimal import Decimal

import pytest

from app.ledger import account_service, voucher_service
from app.ledger.exceptions import VoucherError

BALANCED = [
    {"summary": "收到服务费", "account_code": "1002", "debit": "11300.00", "credit": "0"},
    {"summary": "技术服务收入", "account_code": "5001", "debit": "0", "credit": "11188.12"},
    {"summary": "计提增值税1%", "account_code": "2221", "debit": "0", "credit": "111.88"},
]


def _create(db_session, book, mama_user, lines=BALANCED, **kwargs):
    return voucher_service.create_voucher(
        db_session,
        book_id=book.id,
        voucher_date="2026-08-06",
        lines=lines,
        operator_id=mama_user.id,
        attachment_count=1,
        **kwargs,
    )


def test_create_balanced_draft(db_session, book, mama_user):
    voucher = _create(db_session, book, mama_user)
    assert voucher.status == "draft"
    assert voucher.voucher_no == 1
    assert voucher.period == "2026-08"
    assert voucher.voucher_no_display == "记字第0001号"
    assert voucher.total_debit == Decimal("11300.00")
    assert voucher.total_credit == Decimal("11300.00")
    assert len(voucher.lines) == 3
    assert voucher.lines[0].debit == Decimal("11300.00")


def test_unbalanced_rejected(db_session, book, mama_user):
    bad = [
        {"summary": "付款", "account_code": "1002", "debit": "100.00", "credit": "0"},
        {"summary": "费用", "account_code": "5602", "debit": "0", "credit": "90.00"},
    ]
    with pytest.raises(VoucherError, match="借贷不平衡"):
        _create(db_session, book, mama_user, lines=bad)


def test_single_line_rejected(db_session, book, mama_user):
    with pytest.raises(VoucherError, match="至少需要两条分录"):
        _create(db_session, book, mama_user, lines=BALANCED[:1])


def test_both_sides_rejected(db_session, book, mama_user):
    bad = [
        {"summary": "a", "account_code": "1002", "debit": "10.00", "credit": "10.00"},
        {"summary": "b", "account_code": "5602", "debit": "0", "credit": "10.00"},
    ]
    with pytest.raises(VoucherError, match="不能同时有值"):
        _create(db_session, book, mama_user, lines=bad)


def test_zero_line_rejected(db_session, book, mama_user):
    bad = [
        {"summary": "a", "account_code": "1002", "debit": "0", "credit": "0"},
        {"summary": "b", "account_code": "5602", "debit": "0", "credit": "0"},
    ]
    with pytest.raises(VoucherError, match="不能同时为零"):
        _create(db_session, book, mama_user, lines=bad)


def test_unknown_account_rejected(db_session, book, mama_user):
    bad = [
        {"summary": "a", "account_code": "9999", "debit": "10.00", "credit": "0"},
        {"summary": "b", "account_code": "5602", "debit": "0", "credit": "10.00"},
    ]
    with pytest.raises(VoucherError, match="9999 不存在"):
        _create(db_session, book, mama_user, lines=bad)


def test_inactive_account_rejected(db_session, book, mama_user):
    account_service.set_account_active(db_session, book_id=book.id, code="1403", is_active=False)
    bad = [
        {"summary": "a", "account_code": "1403", "debit": "10.00", "credit": "0"},
        {"summary": "b", "account_code": "5602", "debit": "0", "credit": "10.00"},
    ]
    with pytest.raises(VoucherError, match="已停用"):
        _create(db_session, book, mama_user, lines=bad)


def test_non_leaf_account_rejected(db_session, book, mama_user):
    account_service.create_detail_account(
        db_session, book_id=book.id, parent_code="5602", code="5602.01", name="办公费"
    )
    bad = [
        {"summary": "a", "account_code": "5602", "debit": "10.00", "credit": "0"},
        {"summary": "b", "account_code": "5603", "debit": "0", "credit": "10.00"},
    ]
    with pytest.raises(VoucherError, match="明细科目"):
        _create(db_session, book, mama_user, lines=bad)


def test_missing_summary_rejected(db_session, book, mama_user):
    bad = [
        {"summary": "  ", "account_code": "1002", "debit": "10.00", "credit": "0"},
        {"summary": "b", "account_code": "5602", "debit": "0", "credit": "10.00"},
    ]
    with pytest.raises(VoucherError, match="缺少摘要"):
        _create(db_session, book, mama_user, lines=bad)


def test_attachment_required_for_manual(db_session, book, mama_user):
    with pytest.raises(VoucherError, match="原始凭证"):
        voucher_service.create_voucher(
            db_session,
            book_id=book.id,
            voucher_date="2026-08-06",
            lines=BALANCED,
            operator_id=mama_user.id,
            attachment_count=0,
        )
    carryover_lines = [
        {"summary": "结转", "account_code": "5001", "debit": "10.00", "credit": "0"},
        {"summary": "结转", "account_code": "3103", "debit": "0", "credit": "10.00"},
    ]
    voucher = voucher_service.create_voucher(
        db_session,
        book_id=book.id,
        voucher_date="2026-08-31",
        lines=carryover_lines,
        operator_id=mama_user.id,
        attachment_count=0,
        source="carryover",
    )
    assert voucher.source == "carryover"


def test_period_before_start_rejected(db_session, book, mama_user):
    with pytest.raises(VoucherError, match="启用期间"):
        voucher_service.create_voucher(
            db_session,
            book_id=book.id,
            voucher_date="2026-07-31",
            lines=BALANCED,
            operator_id=mama_user.id,
            attachment_count=1,
        )


def test_amount_roundtrip_exact(db_session, book):
    from app.models.voucher import Voucher

    mama = None
    from app.models.user import User

    mama = User(username="u1", password_hash="x", display_name="u", role="bookkeeper")
    db_session.add(mama)
    db_session.commit()
    voucher = _create(db_session, book, mama)
    db_session.expunge_all()
    loaded = db_session.get(Voucher, voucher.id)
    assert loaded.total_debit == Decimal("11300.00")
    assert loaded.lines[1].credit == Decimal("11188.12")
