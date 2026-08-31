def test_login_success(client, admin_user):
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


def test_login_wrong_password_rejected(client, admin_user):
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "bad"})
    assert resp.status_code == 401


def test_login_unknown_user_rejected(client, admin_user):
    resp = client.post("/api/auth/login", json={"username": "nobody", "password": "x123456"})
    assert resp.status_code == 401


def test_me_requires_token(client):
    assert client.get("/api/auth/me").status_code == 401


def test_me_returns_current_user(client, admin_user, auth_headers):
    resp = client.get("/api/auth/me", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["username"] == "admin"
    assert resp.json()["role"] == "admin"


def test_admin_creates_user(client, admin_user, auth_headers):
    resp = client.post(
        "/api/users",
        headers=auth_headers,
        json={"username": "mama", "password": "mama123456", "display_name": "妈妈", "role": "bookkeeper"},
    )
    assert resp.status_code == 201
    assert resp.json()["role"] == "bookkeeper"


def test_duplicate_username_rejected(client, admin_user, auth_headers):
    body = {"username": "mama", "password": "mama123456", "display_name": "妈妈", "role": "bookkeeper"}
    assert client.post("/api/users", headers=auth_headers, json=body).status_code == 201
    assert client.post("/api/users", headers=auth_headers, json=body).status_code == 409


def test_non_admin_cannot_create_user(client, admin_user, auth_headers):
    client.post(
        "/api/users",
        headers=auth_headers,
        json={"username": "mama", "password": "mama123456", "display_name": "妈妈", "role": "bookkeeper"},
    )
    token = client.post(
        "/api/auth/login", json={"username": "mama", "password": "mama123456"}
    ).json()["access_token"]
    resp = client.post(
        "/api/users",
        headers={"Authorization": f"Bearer {token}"},
        json={"username": "papa", "password": "papa123456", "display_name": "爸爸", "role": "auditor"},
    )
    assert resp.status_code == 403
