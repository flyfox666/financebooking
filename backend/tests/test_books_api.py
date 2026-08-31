from app.ledger.accounts_catalog import ACCOUNT_CATALOG


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_create_book_seeds_66_accounts(client, auth_headers):
    resp = client.post(
        "/api/books",
        headers=auth_headers,
        json={
            "name": "示例科技有限公司",
            "tax_no": "91310000MA1K35X00B",
            "start_period": "2026-08",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["accounting_standard"] == "sme_2011"
    assert body["taxpayer_type"] == "small_scale"
    book_id = body["id"]

    full = client.get(
        "/api/accounts",
        headers=auth_headers,
        params={"book_id": book_id, "only_active": False},
    )
    assert full.status_code == 200
    assert len(full.json()) == 66

    active = client.get("/api/accounts", headers=auth_headers, params={"book_id": book_id})
    expected_active = sum(1 for row in ACCOUNT_CATALOG if row[4])
    assert len(active.json()) == expected_active


def test_create_book_without_token_rejected(client):
    resp = client.post("/api/books", json={"name": "某公司", "start_period": "2026-08"})
    assert resp.status_code == 401


def test_create_book_invalid_period_rejected(client, auth_headers):
    resp = client.post(
        "/api/books", headers=auth_headers, json={"name": "某公司", "start_period": "2026-13"}
    )
    assert resp.status_code == 400
    assert "YYYY-MM" in resp.json()["detail"]


def test_create_book_bad_tax_no_rejected(client, auth_headers):
    resp = client.post(
        "/api/books",
        headers=auth_headers,
        json={"name": "某公司", "tax_no": "123", "start_period": "2026-08"},
    )
    assert resp.status_code == 400


def test_get_book(client, auth_headers, book):
    resp = client.get(f"/api/books/{book.id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["name"] == "测试科技有限公司"


def test_get_missing_book_returns_404(client, auth_headers):
    assert client.get("/api/books/999", headers=auth_headers).status_code == 404


def test_create_detail_account_via_api(client, auth_headers, book):
    resp = client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"book_id": book.id, "parent_code": "5602", "code": "5602.01", "name": "办公费"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["level"] == 2


def test_create_detail_account_invalid_prefix_rejected(client, auth_headers, book):
    resp = client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"book_id": book.id, "parent_code": "5602", "code": "5603.01", "name": "错位"},
    )
    assert resp.status_code == 400


def test_patch_account_disable(client, auth_headers, book):
    resp = client.patch(
        "/api/accounts/1403",
        headers=auth_headers,
        params={"book_id": book.id},
        json={"is_active": False},
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    active_tree = client.get(
        "/api/accounts", headers=auth_headers, params={"book_id": book.id}
    ).json()
    assert "1403" not in {node["code"] for node in active_tree}


def test_non_admin_cannot_manage_accounts(client, auth_headers, book):
    client.post(
        "/api/users",
        headers=auth_headers,
        json={"username": "mama", "password": "mama123456", "display_name": "妈妈", "role": "bookkeeper"},
    )
    token = client.post(
        "/api/auth/login", json={"username": "mama", "password": "mama123456"}
    ).json()["access_token"]
    resp = client.post(
        "/api/accounts",
        headers={"Authorization": f"Bearer {token}"},
        json={"book_id": book.id, "parent_code": "5602", "code": "5602.09", "name": "其他"},
    )
    assert resp.status_code == 403
