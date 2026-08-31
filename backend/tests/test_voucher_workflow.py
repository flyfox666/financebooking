from decimal import Decimal

import pytest

from app.ledger import voucher_service
from app.ledger.exceptions import VoucherError
from app.ledger.mock_data import expected_posted_net, voucher_payloads
from app.models.voucher import Voucher

BALANCED = [
    {"summary": "收到服务费", "account_code": "1002", "debit": "11300.00", "credit": "0"},
    {"summary": "技术服务收入", "account_code": "5001", "debit": "0", "credit": "11188.12"},
    {"summary": "计提增值税1%", "account_code": "2221", "debit": "0", "credit": "111.88"},
]


def test_full_month_lifecycle(mock_month, book, get_posted_nets):
    assert len(mock_month) == 23
    assert [v.voucher_no for v in mock_month] == list(range(1, 24))
    assert all(v.status == "posted" for v in mock_month)
    assert get_posted_nets(book.id) == expected_posted_net()
    assert "2211" not in get_posted_nets(book.id)
    assert "2241" not in get_posted_nets(book.id)


def test_monthly_numbering_restarts(db_session, book, mama_user):
    voucher_service.create_voucher(
        db_session,
        book_id=book.id,
        voucher_date="2026-08-06",
        lines=BALANCED,
        operator_id=mama_user.id,
        attachment_count=1,
    )
    second = voucher_service.create_voucher(
        db_session,
        book_id=book.id,
        voucher_date="2026-09-01",
        lines=BALANCED,
        operator_id=mama_user.id,
        attachment_count=1,
    )
    assert second.period == "2026-09"
    assert second.voucher_no == 1


def test_state_machine_guards(db_session, book, mama_user, auditor_user):
    voucher = voucher_service.create_voucher(
        db_session,
        book_id=book.id,
        voucher_date="2026-08-06",
        lines=BALANCED,
        operator_id=mama_user.id,
        attachment_count=1,
    )
    with pytest.raises(VoucherError, match="待审核"):
        voucher_service.audit_voucher(db_session, voucher_id=voucher.id, operator=auditor_user)
    voucher_service.submit_voucher(db_session, voucher_id=voucher.id, operator_id=mama_user.id)
    with pytest.raises(VoucherError, match="同一人"):
        voucher_service.audit_voucher(db_session, voucher_id=voucher.id, operator=mama_user)
    voucher_service.audit_voucher(db_session, voucher_id=voucher.id, operator=auditor_user)
    with pytest.raises(VoucherError, match="草稿凭证可以修改"):
        voucher_service.update_voucher(db_session, voucher_id=voucher.id, attachment_count=3)
    voucher_service.post_voucher(db_session, voucher_id=voucher.id, operator=auditor_user)
    with pytest.raises(VoucherError, match="已审核凭证可以过账"):
        voucher_service.post_voucher(db_session, voucher_id=voucher.id, operator=auditor_user)
    voucher_service.unpost_voucher(db_session, voucher_id=voucher.id)
    assert voucher.status == "audited"
    voucher_service.post_voucher(db_session, voucher_id=voucher.id, operator=auditor_user)
    assert voucher.status == "posted"


def test_delete_only_draft(db_session, book, mama_user, auditor_user):
    draft = voucher_service.create_voucher(
        db_session,
        book_id=book.id,
        voucher_date="2026-08-06",
        lines=BALANCED,
        operator_id=mama_user.id,
        attachment_count=1,
    )
    voucher_service.delete_voucher(db_session, voucher_id=draft.id)
    assert db_session.get(Voucher, draft.id) is None

    posted = voucher_service.create_voucher(
        db_session,
        book_id=book.id,
        voucher_date="2026-08-06",
        lines=BALANCED,
        operator_id=mama_user.id,
        attachment_count=1,
    )
    voucher_service.submit_voucher(db_session, voucher_id=posted.id, operator_id=mama_user.id)
    voucher_service.audit_voucher(db_session, voucher_id=posted.id, operator=auditor_user)
    voucher_service.post_voucher(db_session, voucher_id=posted.id, operator=auditor_user)
    with pytest.raises(VoucherError, match="草稿凭证可以删除"):
        voucher_service.delete_voucher(db_session, voucher_id=posted.id)


def test_reversal_flow(db_session, book, mama_user, auditor_user, admin_user, post_flow, get_posted_nets, contacts_pair):
    from app.ledger.mock_data import setup_detail_accounts
    from tests.conftest import _inject_contact

    setup_detail_accounts(db_session, book.id)
    vouchers = []
    for payload in voucher_payloads()[:6]:
        for line in payload["lines"]:
            _inject_contact(line, contacts_pair)
        voucher = voucher_service.create_voucher(
            db_session, book_id=book.id, operator_id=mama_user.id, **payload
        )
        post_flow(voucher, mama_user, auditor_user)
        vouchers.append(voucher)
    original = vouchers[4]
    assert original.voucher_no == 5

    red = voucher_service.reverse_voucher(db_session, voucher_id=original.id, operator=auditor_user)
    assert red.status == "draft"
    assert red.source == "reverse"
    assert red.reverses_voucher_id == original.id
    assert red.total_debit == Decimal("-11300.00")
    assert red.lines[0].debit == Decimal("-11300.00")
    assert red.lines[0].summary.startswith("冲销记字第0005号：")

    post_flow(red, mama_user, admin_user)
    assert red.status == "posted"
    assert original.status == "voided"
    assert original.voided_by_voucher_id == red.id

    nets = get_posted_nets(book.id)
    assert nets["1122"] == Decimal("-11300.00")
    assert nets["1002"] == Decimal("487600.00")
    assert "5001" not in nets
    assert "2221" not in nets

    with pytest.raises(VoucherError, match="红字冲销"):
        voucher_service.reverse_voucher(db_session, voucher_id=original.id, operator=auditor_user)
    with pytest.raises(VoucherError, match="反过账"):
        voucher_service.unpost_voucher(db_session, voucher_id=original.id)


def test_draft_period_move_renumbers(db_session, book, mama_user):
    voucher = voucher_service.create_voucher(
        db_session,
        book_id=book.id,
        voucher_date="2026-08-05",
        lines=BALANCED,
        operator_id=mama_user.id,
        attachment_count=1,
    )
    moved = voucher_service.update_voucher(
        db_session, voucher_id=voucher.id, voucher_date="2026-09-05"
    )
    assert moved.period == "2026-09"
    assert moved.voucher_no == 1
