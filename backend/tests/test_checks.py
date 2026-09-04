"""勾稽关系校验（G14）：方向/余额异常、未分配利润↔净利润、货币资金↔现金流、跨期衔接。"""

from app.ledger import check_service, voucher_service


def test_direction_anomalies_cash_negative(db_session, mock_month, book, mama_user, auditor_user, post_flow):
    """现金支出超过余额导致货币资金赤字 → 方向异常（severity=high）。"""
    voucher = voucher_service.create_voucher(
        db_session, book_id=book.id, voucher_date="2026-08-26", attachment_count=1,
        lines=[
            {"summary": "现金大额支出", "account_code": "5602.01", "debit": "1000", "credit": "0"},
            {"summary": "现金付款", "account_code": "1001", "debit": "0", "credit": "1000"},
        ],
        operator_id=mama_user.id,
    )
    post_flow(voucher, mama_user, auditor_user)

    issues = check_service.direction_anomalies(db_session, book_id=book.id, period="2026-08")
    cash = [i for i in issues if i["code"].startswith("1001")]
    assert cash, "现金应出现方向异常"
    assert cash[0]["issue"] == "货币资金赤字"
    assert cash[0]["severity"] == "high"


def test_direction_no_anomalies_when_clean(db_session, mock_month, book):
    """正常账套（mock_month）无方向异常。"""
    issues = check_service.direction_anomalies(db_session, book_id=book.id, period="2026-08")
    assert issues == []


def test_profit_and_cash_reconciliation_ok(db_session, mock_month, book):
    """无利润分配/盈余公积变动时：未分配利润勾稽、货币资金↔现金流均通过。"""
    profit = check_service.profit_reconciliation(db_session, book_id=book.id, period="2026-08")
    assert profit["ok"] is True

    cash = check_service.cash_reconciliation(db_session, book_id=book.id, period="2026-08")
    assert cash["ok"] is True


def test_period_continuity_first_and_next(db_session, mock_month, book):
    """启用首期跳过；次期期初应等于上期期末。"""
    first = check_service.period_continuity(db_session, book_id=book.id, period="2026-08")
    assert first["ok"] is True
    assert first["note"] == "启用首期，无上期可比"

    nextp = check_service.period_continuity(db_session, book_id=book.id, period="2026-09")
    assert nextp["ok"] is True


def test_run_checks_aggregates(db_session, mock_month, book):
    """聚合接口返回四类校验与 all_ok。"""
    result = check_service.run_checks(db_session, book_id=book.id, period="2026-08")
    assert set(result) >= {"direction", "profit", "cash", "continuity", "all_ok"}
    assert result["all_ok"] is True


def test_checks_api(client, auth_headers, mock_month, book):
    """API 端点可访问并返回勾稽结果。"""
    resp = client.get(f"/api/reports/checks?book_id={book.id}&period=2026-08", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "direction" in data and "all_ok" in data