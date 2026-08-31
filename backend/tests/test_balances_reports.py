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
