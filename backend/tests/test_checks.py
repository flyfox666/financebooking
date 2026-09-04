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


def test_period_continuity_first_and_unclosed(db_session, mock_month, book):
    """启用首期跳过；上期未结账（无快照）明确标注跳过（快照语义）。"""
    first = check_service.period_continuity(db_session, book_id=book.id, period="2026-08")
    assert first["ok"] is True
    assert first["note"] == "启用首期，无上期可比"

    nextp = check_service.period_continuity(db_session, book_id=book.id, period="2026-09")
    assert nextp["ok"] is True
    assert "未结账" in nextp["note"]


def test_period_continuity_snapshot_catches_tamper(db_session, mock_month, book, mama_user):
    """结账后篡改期初 → 跨期校验应 ❌（对比基准是结账快照而非现算值）；恢复后 ✅。"""
    from decimal import Decimal

    from app.ledger import close_service
    from app.models.report import OpeningBalance

    close_service.close_period(db_session, book_id=book.id, period="2026-08", operator_id=mama_user.id)

    # 结账后篡改 1002 期初
    ob = db_session.query(OpeningBalance).filter(
        OpeningBalance.book_id == book.id, OpeningBalance.account_code == "1002"
    ).first()
    if ob is None:
        ob = OpeningBalance(book_id=book.id, account_code="1002", debit=Decimal("0"), credit=Decimal("0"))
        db_session.add(ob)
    orig_d = ob.debit
    ob.debit = Decimal(str(ob.debit)) + Decimal("777")
    db_session.commit()

    result = check_service.period_continuity(db_session, book_id=book.id, period="2026-09")
    hit = [i for i in result["issues"] if i["code"] == "1002"]
    assert hit, "期初被篡改应被抓到"
    assert result["ok"] is False

    # 恢复 → ✅
    ob.debit = orig_d
    db_session.commit()
    assert check_service.period_continuity(db_session, book_id=book.id, period="2026-09")["ok"] is True


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