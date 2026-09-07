import pytest

from app.ledger import balances
from app.ledger.exceptions import VoucherError


def rows_by_code(report):
    return {row["account_code"]: row for row in report["rows"]}


def test_trial_balance_six_columns(db_session, mock_month, book):
    report = balances.trial_balance(db_session, book_id=book.id, period="2026-08")
    assert report["is_balanced"] is True
    rows = rows_by_code(report)
    assert len(report["rows"]) == 73
    assert "5602" not in rows

    bank = rows["1002"]
    assert bank["opening_debit"] == "0.00"
    assert bank["opening_credit"] == "0.00"
    assert bank["period_debit"] == "561300.00"
    assert bank["period_credit"] == "96930.00"
    assert bank["closing_debit"] == "464370.00"
    assert bank["closing_credit"] == "0.00"

    salary = rows["2211"]
    assert salary["period_debit"] == "45000.00"
    assert salary["period_credit"] == "45000.00"
    assert salary["closing_debit"] == "0.00"
    assert salary["closing_credit"] == "0.00"

    totals = report["totals"]
    assert totals["opening_debit"] == "0.00"
    assert totals["period_debit"] == totals["period_credit"]
    assert totals["closing_debit"] == totals["closing_credit"]


def test_general_ledger_rollup(db_session, mock_month, book):
    report = balances.general_ledger(db_session, book_id=book.id, period="2026-08")
    rows = rows_by_code(report)
    assert len(report["rows"]) == 66
    assert rows["1002"]["period_debit"] == "561300.00"
    assert rows["1002"]["period_credit"] == "96930.00"
    assert rows["1002"]["closing_debit"] == "464370.00"
    assert rows["5602"]["period_debit"] == "40041.33"
    assert rows["5602"]["period_credit"] == "0.00"
    assert rows["5001"]["period_credit"] == "27227.72"


def test_detail_ledger_running_balance(db_session, mock_month, book):
    report = balances.detail_ledger(
        db_session, book_id=book.id, account_code="1002", period_from="2026-08", period_to="2026-08"
    )
    assert report["closing_balance"] == "464370.00"
    assert report["closing_direction"] == "借"
    assert len(report["rows"]) == 16
    assert report["rows"][0]["row_type"] == "opening"
    assert report["rows"][0]["balance"] == "0.00"
    assert report["rows"][0]["balance_direction"] == "平"
    assert report["rows"][1]["debit"] == "500000.00"
    assert report["rows"][1]["balance"] == "500000.00"
    assert report["rows"][2]["credit"] == "12000.00"
    assert report["rows"][2]["balance"] == "488000.00"


def test_detail_ledger_credit_direction_account(db_session, mock_month, book):
    report = balances.detail_ledger(
        db_session, book_id=book.id, account_code="5001", period_from="2026-08", period_to="2026-08"
    )
    assert report["direction"] == -1
    assert report["closing_balance"] == "27227.72"
    assert report["closing_direction"] == "贷"
    assert len(report["rows"]) == 4


def test_detail_ledger_requires_leaf(db_session, mock_month, book):
    with pytest.raises(VoucherError, match="末级科目"):
        balances.detail_ledger(
            db_session,
            book_id=book.id,
            account_code="5602",
            period_from="2026-08",
            period_to="2026-08",
        )


def _make_unposted(db_session, book, mama_user, auditor_user, account_code, total, status):
    """造一张未过账凭证（费用借方 + 银行贷方），可指定 draft/submitted/audited 状态。"""
    from app.ledger import voucher_service

    voucher = voucher_service.create_voucher(
        db_session, book_id=book.id, voucher_date="2026-08-20", attachment_count=1,
        lines=[
            {"summary": f"未过账-{account_code}", "account_code": account_code, "debit": total, "credit": "0"},
            {"summary": "银行付款", "account_code": "1002", "debit": "0", "credit": total},
        ],
        operator_id=mama_user.id,
    )
    from tests.conftest import attach_original
    attach_original(db_session, voucher, mama_user.id)
    if status in ("submitted", "audited"):
        voucher_service.submit_voucher(db_session, voucher_id=voucher.id, operator_id=mama_user.id)
    if status == "audited":
        voucher_service.audit_voucher(db_session, voucher_id=voucher.id, operator=auditor_user)
    return voucher


def test_trial_balance_unposted_none_returns_zero(db_session, mock_month, book):
    """默认不含未过账：所有行未过账列全 0，account 状态标记为 none（历史行为不变）。"""
    report = balances.trial_balance(db_session, book_id=book.id, period="2026-08")
    assert report["unposted"] == "none"
    assert report["totals"]["unposted_debit"] == "0.00"
    assert report["totals"]["unposted_credit"] == "0.00"
    rows = rows_by_code(report)
    assert all(row["unposted_debit"] == "0.00" and row["unposted_credit"] == "0.00" for row in report["rows"])
    assert report["is_balanced"] is True


def test_trial_balance_unposted_pending_and_all(db_session, mock_month, book, mama_user, auditor_user):
    """三档口径：pending 只含待审+已审；all 再含草稿；none 全零。"""
    _make_unposted(db_session, book, mama_user, auditor_user, "5602.01", "500.00", "submitted")
    _make_unposted(db_session, book, mama_user, auditor_user, "5602.02", "300.00", "audited")
    _make_unposted(db_session, book, mama_user, auditor_user, "5602.03", "200.00", "draft")

    none = rows_by_code(balances.trial_balance(db_session, book_id=book.id, period="2026-08"))
    pending = rows_by_code(balances.trial_balance(db_session, book_id=book.id, period="2026-08", unposted="pending"))
    allrows = rows_by_code(balances.trial_balance(db_session, book_id=book.id, period="2026-08", unposted="all"))

    # none：不计未过账
    assert none["5602.01"]["unposted_debit"] == "0.00"
    # pending：submitted + audited 计入，draft 不计
    assert pending["5602.01"]["unposted_debit"] == "500.00"
    assert pending["5602.02"]["unposted_debit"] == "300.00"
    assert pending["5602.03"]["unposted_debit"] == "0.00"
    # all：额外含 draft
    assert allrows["5602.03"]["unposted_debit"] == "200.00"


def test_trial_balance_unposted_not_merged(db_session, mock_month, book, mama_user, auditor_user):
    """未过账金额走独立列，不并入已过账三栏，且不破坏平衡。"""
    before = rows_by_code(balances.trial_balance(db_session, book_id=book.id, period="2026-08"))
    _make_unposted(db_session, book, mama_user, auditor_user, "5602.01", "500.00", "submitted")
    after = rows_by_code(balances.trial_balance(db_session, book_id=book.id, period="2026-08", unposted="pending"))

    # 已过账发生额不变（未过账只进 unposted 列）
    assert before["5602.01"]["period_debit"] == after["5602.01"]["period_debit"]
    # 未过账金额出现在独立列
    assert after["5602.01"]["unposted_debit"] == "500.00"
    # 已过账三栏仍平衡
    report = balances.trial_balance(db_session, book_id=book.id, period="2026-08", unposted="pending")
    assert report["is_balanced"] is True
