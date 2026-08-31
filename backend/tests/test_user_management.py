def test_list_users_admin_only(client, admin_user, auth_headers):
    listing = client.get("/api/users", headers=auth_headers).json()
    assert [u["username"] for u in listing] == ["admin"]
    assert listing[0]["is_active"] is True

    client.post(
        "/api/users",
        headers=auth_headers,
        json={"username": "mama", "password": "mama123456", "display_name": "妈妈", "role": "bookkeeper"},
    )
    mama = client.post("/api/auth/login", json={"username": "mama", "password": "mama123456"})
    mama_headers = {"Authorization": f"Bearer {mama.json()['access_token']}"}
    assert client.get("/api/users", headers=mama_headers).status_code == 403


def test_patch_user_role_and_password(client, admin_user, auth_headers):
    created = client.post(
        "/api/users",
        headers=auth_headers,
        json={"username": "papa", "password": "papa123456", "display_name": "爸爸", "role": "bookkeeper"},
    )
    user_id = created.json()["id"]

    patched = client.patch(
        f"/api/users/{user_id}",
        headers=auth_headers,
        json={"role": "auditor", "display_name": "老张"},
    ).json()
    assert patched["role"] == "auditor"
    assert patched["display_name"] == "老张"

    client.patch(f"/api/users/{user_id}", headers=auth_headers, json={"password": "newpass654321"})
    resp = client.post("/api/auth/login", json={"username": "papa", "password": "newpass654321"})
    assert resp.status_code == 200


def test_disable_and_delete_guards(client, admin_user, auth_headers):
    created = client.post(
        "/api/users",
        headers=auth_headers,
        json={"username": "mama", "password": "mama123456", "display_name": "妈妈", "role": "bookkeeper"},
    )
    mama_id = created.json()["id"]

    resp = client.patch(f"/api/users/{admin_user.id}", headers=auth_headers, json={"is_active": False})
    assert resp.status_code == 400
    assert "自己" in resp.json()["detail"]

    resp = client.patch(f"/api/users/{admin_user.id}", headers=auth_headers, json={"role": "bookkeeper"})
    assert resp.status_code == 400
    assert "最后一个管理员" in resp.json()["detail"]

    assert client.delete(f"/api/users/{admin_user.id}", headers=auth_headers).status_code == 400

    client.patch(f"/api/users/{mama_id}", headers=auth_headers, json={"is_active": False})
    resp = client.post("/api/auth/login", json={"username": "mama", "password": "mama123456"})
    assert resp.status_code == 401

    assert client.delete(f"/api/users/{mama_id}", headers=auth_headers).status_code == 204
    listing = client.get("/api/users", headers=auth_headers).json()
    assert len(listing) == 1


def test_get_opening_returns_saved_items(client, auth_headers, book):
    body = {"items": [{"account_code": "1002", "debit": "100000.00", "credit": "0"},
                      {"account_code": "3001", "debit": "0", "credit": "100000.00"}]}
    client.put(f"/api/books/{book.id}/opening", headers=auth_headers, json=body)
    rows = client.get(f"/api/books/{book.id}/opening", headers=auth_headers).json()
    by_code = {row["account_code"]: row for row in rows}
    assert by_code["1002"]["debit"].startswith("100000")
    assert by_code["3001"]["credit"].startswith("100000")
