from app.ledger.mock_data import setup_detail_accounts


def income_payload(contacts_pair):
    customer_id = contacts_pair["customer"].id
    return {
        "voucher_date": "2026-08-06",
        "attachment_count": 1,
        "source": "manual",
        "lines": [
            {"summary": "开票应收技术服务费", "account_code": "1122", "debit": "11300.00", "credit": "0", "contact_id": customer_id},
            {"summary": "确认技术服务收入", "account_code": "5001", "debit": "0", "credit": "11188.12"},
            {"summary": "计提增值税1%", "account_code": "2221", "debit": "0", "credit": "111.88"},
        ],
    }


def _headers(client, username, password):
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_create_requires_auth(client, book):
    resp = client.post(
        "/api/vouchers", params={"book_id": book.id},
        json={"voucher_date": "2026-08-06", "lines": [
            {"summary": "a", "account_code": "1002", "debit": "1", "credit": "0"},
            {"summary": "b", "account_code": "5603", "debit": "0", "credit": "1"},
        ]},
    )
    assert resp.status_code == 401


def test_full_http_flow(client, book, admin_user, mama_user, auditor_user, contacts_pair):
    mama_h = _headers(client, "mama", "mama123456")
    papa_h = _headers(client, "papa", "papa123456")
    admin_h = _headers(client, "admin", "admin123")

    resp = client.post(
        "/api/vouchers", params={"book_id": book.id}, headers=mama_h, json=income_payload(contacts_pair)
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    voucher_id = data["id"]
    assert data["voucher_no_display"] == "记字第0001号"
    assert data["status"] == "draft"
    assert data["total_debit"] == "11300.00"
    assert data["lines"][0]["debit"] == "11300.00"

    resp = client.post(f"/api/vouchers/{voucher_id}/submit", headers=mama_h)
    assert resp.status_code == 200
    assert resp.json()["status"] == "submitted"

    resp = client.post(f"/api/vouchers/{voucher_id}/audit", headers=mama_h)
    assert resp.status_code == 403

    resp = client.post(f"/api/vouchers/{voucher_id}/audit", headers=admin_h)
    assert resp.status_code == 200
    assert resp.json()["status"] == "audited"

    resp = client.post(f"/api/vouchers/{voucher_id}/post", headers=mama_h)
    assert resp.status_code == 403

    resp = client.post(f"/api/vouchers/{voucher_id}/post", headers=papa_h)
    assert resp.status_code == 200
    assert resp.json()["status"] == "posted"

    resp = client.get(
        "/api/vouchers", headers=admin_h, params={"book_id": book.id, "status": "posted"}
    )
    assert voucher_id in [v["id"] for v in resp.json()]

    detail = client.get(f"/api/vouchers/{voucher_id}", headers=admin_h).json()
    assert detail["lines"][1]["credit"] == "11188.12"


def test_creator_cannot_self_audit(client, book, admin_user, mama_user, auditor_user, contacts_pair):
    admin_h = _headers(client, "admin", "admin123")
    papa_h = _headers(client, "papa", "papa123456")

    resp = client.post(
        "/api/vouchers", params={"book_id": book.id}, headers=admin_h, json=income_payload(contacts_pair)
    )
    voucher_id = resp.json()["id"]
    client.post(f"/api/vouchers/{voucher_id}/submit", headers=admin_h)
    resp = client.post(f"/api/vouchers/{voucher_id}/audit", headers=admin_h)
    assert resp.status_code == 400
    assert "同一人" in resp.json()["detail"]

    resp = client.post(f"/api/vouchers/{voucher_id}/audit", headers=papa_h)
    assert resp.status_code == 200


def test_patch_and_delete_draft_only(client, book, admin_user, mama_user, auditor_user, contacts_pair):
    admin_h = _headers(client, "admin", "admin123")

    resp = client.post(
        "/api/vouchers", params={"book_id": book.id}, headers=admin_h, json=income_payload(contacts_pair)
    )
    draft_id = resp.json()["id"]

    resp = client.patch(
        f"/api/vouchers/{draft_id}", headers=admin_h, json={"attachment_count": 2}
    )
    assert resp.status_code == 200
    assert resp.json()["attachment_count"] == 2

    new_lines = income_payload(contacts_pair)
    new_lines["lines"] = [
        {"summary": "买办公用品", "account_code": "5602", "debit": "100.00", "credit": "0"},
        {"summary": "银行付款", "account_code": "1002", "debit": "0", "credit": "100.00"},
    ]
    resp = client.patch(f"/api/vouchers/{draft_id}", headers=admin_h, json=new_lines)
    assert resp.status_code == 200
    assert resp.json()["total_debit"] == "100.00"

    resp = client.delete(f"/api/vouchers/{draft_id}", headers=admin_h)
    assert resp.status_code == 204

    mama_h = _headers(client, "mama", "mama123456")
    papa_h = _headers(client, "papa", "papa123456")
    resp = client.post(
        "/api/vouchers", params={"book_id": book.id}, headers=mama_h, json=income_payload(contacts_pair)
    )
    posted_id = resp.json()["id"]
    client.post(f"/api/vouchers/{posted_id}/submit", headers=mama_h)
    client.post(f"/api/vouchers/{posted_id}/audit", headers=papa_h)
    client.post(f"/api/vouchers/{posted_id}/post", headers=papa_h)

    resp = client.patch(f"/api/vouchers/{posted_id}", headers=admin_h, json={"attachment_count": 2})
    assert resp.status_code == 400
    resp = client.delete(f"/api/vouchers/{posted_id}", headers=admin_h)
    assert resp.status_code == 400


def test_reverse_endpoint(client, book, admin_user, mama_user, auditor_user, get_posted_nets, db_session, contacts_pair):
    mama_h = _headers(client, "mama", "mama123456")
    papa_h = _headers(client, "papa", "papa123456")
    admin_h = _headers(client, "admin", "admin123")

    resp = client.post(
        "/api/vouchers", params={"book_id": book.id}, headers=mama_h, json=income_payload(contacts_pair)
    )
    voucher_id = resp.json()["id"]
    client.post(f"/api/vouchers/{voucher_id}/submit", headers=mama_h)
    client.post(f"/api/vouchers/{voucher_id}/audit", headers=papa_h)
    client.post(f"/api/vouchers/{voucher_id}/post", headers=papa_h)

    resp = client.post(f"/api/vouchers/{voucher_id}/reverse", headers=mama_h)
    assert resp.status_code == 403

    resp = client.post(f"/api/vouchers/{voucher_id}/reverse", headers=papa_h)
    assert resp.status_code == 201, resp.text
    red = resp.json()
    assert red["status"] == "draft"
    assert red["total_debit"] == "-11300.00"
    assert red["reverses_voucher_id"] == voucher_id

    red_id = red["id"]
    client.post(f"/api/vouchers/{red_id}/submit", headers=papa_h)
    client.post(f"/api/vouchers/{red_id}/audit", headers=admin_h)
    client.post(f"/api/vouchers/{red_id}/post", headers=papa_h)

    original = client.get(f"/api/vouchers/{voucher_id}", headers=papa_h).json()
    assert original["status"] == "voided"
    assert original["voided_by_voucher_id"] == red_id


def test_carryover_endpoint(
    client, db_session, book, admin_user, mama_user, auditor_user, auth_headers, contacts_pair
):
    setup_detail_accounts(db_session, book.id)
    mama_h = _headers(client, "mama", "mama123456")
    papa_h = _headers(client, "papa", "papa123456")

    resp = client.post(
        "/api/vouchers", params={"book_id": book.id}, headers=mama_h, json=income_payload(contacts_pair)
    )
    voucher_id = resp.json()["id"]
    client.post(f"/api/vouchers/{voucher_id}/submit", headers=mama_h)
    client.post(f"/api/vouchers/{voucher_id}/audit", headers=papa_h)
    client.post(f"/api/vouchers/{voucher_id}/post", headers=papa_h)

    resp = client.post(
        "/api/periods/2026-08/carryover", headers=auth_headers, params={"book_id": book.id}
    )
    assert resp.status_code == 201, resp.text
    drafts = resp.json()
    assert [v["carryover_type"] for v in drafts] == ["pnl"]
    pnl = drafts[0]
    assert pnl["status"] == "draft"
    assert pnl["total_debit"] == "11188.12"

    resp = client.post(
        "/api/periods/2026-08/carryover", headers=auth_headers, params={"book_id": book.id}
    )
    assert resp.status_code == 400

    pnl_id = pnl["id"]
    client.post(f"/api/vouchers/{pnl_id}/submit", headers=mama_h)
    client.post(f"/api/vouchers/{pnl_id}/audit", headers=papa_h)
    client.post(f"/api/vouchers/{pnl_id}/post", headers=papa_h)
    nets_dto = client.get(
        "/api/vouchers", headers=auth_headers, params={"book_id": book.id, "status": "posted"}
    ).json()
    assert any(v["carryover_type"] == "pnl" and v["status"] == "posted" for v in nets_dto)
