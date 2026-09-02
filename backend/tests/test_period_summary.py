"""G7 本期概要：凭证状态计数与 KPI 取数一致性；空期间全零。"""


def test_period_summary_counts_and_kpi(client, auth_headers, book, mock_month):
    resp = client.get(
        "/api/reports/period-summary", headers=auth_headers,
        params={"book_id": book.id, "period": "2026-08"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["voucher_status"]["posted"] == len(mock_month)
    assert data["voucher_status"]["draft"] == 0
    # KPI 与报表取数一致（金额为两位小数字符串，前端契约）
    assert data["operating_revenue"] == "27227.72"
    assert data["net_profit"] == "-16133.61"          # 未结转，表结法
    assert data["monetary_funds"] == "465020.00"


def test_period_summary_empty_period_zero(client, auth_headers, book):
    resp = client.get(
        "/api/reports/period-summary", headers=auth_headers,
        params={"book_id": book.id, "period": "2026-09"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["voucher_status"] == {"draft": 0, "submitted": 0, "audited": 0, "posted": 0}
    for key in (
        "operating_revenue", "operating_cost", "net_profit", "monetary_funds",
        "accounts_receivable", "accounts_payable", "total_assets", "undistributed_profits",
    ):
        assert data[key] == "0.00", key
