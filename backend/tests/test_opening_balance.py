OPENING_ITEMS = {
    "items": [
        {"account_code": "1002", "debit": "100000.00", "credit": "0"},
        {"account_code": "3001", "debit": "0", "credit": "100000.00"},
    ]
}


def test_set_opening_and_reports(client, auth_headers, book):
    resp = client.put(f"/api/books/{book.id}/opening", headers=auth_headers, json=OPENING_ITEMS)
    assert resp.status_code == 200, resp.text
    assert resp.json()["total_debit"] == "100000.00"
    assert resp.json()["total_credit"] == "100000.00"

    tb = client.get(
        "/api/reports/trial-balance",
        headers=auth_headers,
        params={"book_id": book.id, "period": "2026-08"},
    ).json()
    assert tb["is_balanced"] is True
    rows = {r["account_code"]: r for r in tb["rows"]}
    assert rows["1002"]["opening_debit"] == "100000.00"
    assert rows["1002"]["closing_debit"] == "100000.00"
    assert rows["3001"]["opening_credit"] == "100000.00"
    assert tb["totals"]["opening_debit"] == "100000.00"
    assert tb["totals"]["opening_credit"] == "100000.00"

    bs = client.get(
        "/api/reports/balance-sheet",
        headers=auth_headers,
        params={"book_id": book.id, "period": "2026-08"},
    ).json()
    assert bs["total_assets"] == "100000.00"
    assert bs["total_liabilities_and_equity"] == "100000.00"
    by_name = {r["name"]: r for r in bs["rows"]}
    assert by_name["货币资金"]["year_begin"] == "100000.00"
    assert by_name["实收资本"]["year_begin"] == "100000.00"


def test_opening_unbalanced_rejected(client, auth_headers, book):
    body = {"items": [{"account_code": "1002", "debit": "100.00", "credit": "0"}]}
    resp = client.put(f"/api/books/{book.id}/opening", headers=auth_headers, json=body)
    assert resp.status_code == 400
    assert "试算不平衡" in resp.json()["detail"]


def test_opening_parent_account_rejected(client, db_session, auth_headers, book):
    from app.ledger import account_service

    account_service.create_detail_account(
        db_session, book_id=book.id, parent_code="5602", code="5602.01", name="办公费"
    )
    body = {"items": [{"account_code": "5602", "debit": "50.00", "credit": "0"}]}
    resp = client.put(f"/api/books/{book.id}/opening", headers=auth_headers, json=body)
    assert resp.status_code == 400
    assert "末级科目" in resp.json()["detail"]


def test_opening_blocked_after_posted_vouchers(client, auth_headers, mock_month, book):
    resp = client.put(f"/api/books/{book.id}/opening", headers=auth_headers, json=OPENING_ITEMS)
    assert resp.status_code == 400
    assert "过账凭证" in resp.json()["detail"]
