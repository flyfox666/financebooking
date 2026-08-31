import json
from decimal import Decimal

from sqlalchemy import select

from app.ledger import balances, report_service
from app.models.account import Account
from app.models.report import ReportTemplate


def row_map(statement):
    return {row["key"]: row for row in statement["rows"]}


def test_balance_sheet_exact_numbers_and_identity(db_session, mock_month_with_carryover, book):
    bs = report_service.balance_sheet(db_session, book_id=book.id, period="2026-08")
    rows = row_map(bs)
    assert rows["monetary_funds"]["closing"] == "465020.00"
    assert rows["accounts_receivable"]["closing"] == "16200.00"
    assert rows["other_receivables"]["closing"] == "4200.00"
    assert rows["inventory"]["closing"] == "0.00"
    assert rows["fixed_asset_origin"]["closing"] == "20500.00"
    assert rows["less_accumulated_depreciation"]["closing"] == "333.33"
    assert rows["fixed_asset_net"]["closing"] == "20166.67"
    assert rows["total_current_assets"]["closing"] == "485420.00"
    assert rows["total_assets"]["closing"] == "505586.67"
    assert rows["accounts_payable"]["closing"] == "998.00"
    assert rows["advances_from_customers"]["closing"] == "50000.00"
    assert rows["taxes_payable"]["closing"] == "722.28"
    assert rows["total_liabilities"]["closing"] == "51720.28"
    assert rows["paid_in_capital"]["closing"] == "500000.00"
    assert rows["undistributed_profits"]["closing"] == "-46133.61"
    assert rows["total_owners_equity"]["closing"] == "453866.39"
    assert bs["total_assets"] == bs["total_liabilities_and_equity"] == "505586.67"
    assert bs["year_begin_total_assets"] == "0.00"
    assert bs["year_begin_total_liabilities_and_equity"] == "0.00"
    assert all(row["year_begin"] == "0.00" for row in bs["rows"])


def test_balance_sheet_covers_every_non_pnl_leaf_once(db_session, mock_month_with_carryover, book):
    template_rows = db_session.scalars(
        select(ReportTemplate).where(
            ReportTemplate.book_id == book.id, ReportTemplate.report == "bs"
        )
    ).all()
    formula_codes = []
    for row in template_rows:
        if row.kind == "line":
            formula_codes.extend(code for code, _ in json.loads(row.formula))

    leaves = db_session.scalars(
        select(Account).where(Account.book_id == book.id, Account.is_leaf.is_(True))
    ).all()
    non_pnl = [a for a in leaves if a.category != "pnl"]
    assert len(non_pnl) == 55
    for account in non_pnl:
        matches = [f for f in formula_codes if account.code == f or account.code.startswith(f + ".")]
        assert len(matches) == 1, f"科目 {account.code} 被覆盖 {len(matches)} 次"


def test_income_statement_after_carryover(db_session, mock_month_with_carryover, book):
    statement = report_service.income_statement(db_session, book_id=book.id, period="2026-08")
    rows = row_map(statement)
    assert rows["operating_revenue"]["month"] == "27227.72"
    assert rows["operating_cost"]["month"] == "3200.00"
    assert rows["administrative_expenses"]["month"] == "70041.33"
    assert rows["financial_expenses"]["month"] == "120.00"
    assert rows["operating_profit"]["month"] == "-46133.61"
    assert rows["total_profit"]["month"] == "-46133.61"
    assert rows["net_profit"]["month"] == "-46133.61"
    assert statement["ytd_net_profit"] == "-46133.61"


def test_income_statement_before_carryover_identical(db_session, mock_month, book):
    statement = report_service.income_statement(db_session, book_id=book.id, period="2026-08")
    rows = row_map(statement)
    assert rows["operating_revenue"]["month"] == "27227.72"
    assert rows["administrative_expenses"]["month"] == "40041.33"
    assert rows["net_profit"]["month"] == "-16133.61"


def test_net_profit_matches_profit_account_movement(db_session, mock_month_with_carryover, book):
    sums = balances.gross_sums(db_session, book.id, "2026-01", "2026-08")
    debit, credit = sums.get("3103", [Decimal("0.00"), Decimal("0.00")])
    assert credit - debit == Decimal("-46133.61")
