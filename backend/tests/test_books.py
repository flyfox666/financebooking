"""G15 账套管理与切换：user_book 成员制 + 可见账套列表 + 访问拦截。"""

from app.ledger import book_service
from app.models.book import UserBook


def test_visible_books_admin_sees_all(db_session, book, mama_user, admin_user):
    """全局 admin 可见全部账套（不依赖成员表）。"""
    books = book_service.visible_books(db_session, admin_user)
    assert [b.id for b in books] == [book.id]


def test_visible_books_member_sees_only_authorized(db_session, book, mama_user):
    """非 admin 用户：无成员关系时看不到任何账套；挂成员后可见。"""
    assert book_service.visible_books(db_session, mama_user) == []

    book_service.add_member(db_session, user_id=mama_user.id, book_id=book.id, role="bookkeeper")
    books = book_service.visible_books(db_session, mama_user)
    assert [b.id for b in books] == [book.id]


def test_user_can_access(db_session, book, mama_user, admin_user):
    """访问判定：admin 恒通；成员通；非成员 403 语义（False）。"""
    assert book_service.user_can_access(db_session, admin_user, book.id) is True
    assert book_service.user_can_access(db_session, mama_user, book.id) is False

    book_service.add_member(db_session, user_id=mama_user.id, book_id=book.id, role="auditor")
    assert book_service.user_can_access(db_session, mama_user, book.id) is True


def test_add_member_idempotent(db_session, book, mama_user):
    """重复挂成员为更新角色而非报错/重复行。"""
    book_service.add_member(db_session, user_id=mama_user.id, book_id=book.id, role="bookkeeper")
    book_service.add_member(db_session, user_id=mama_user.id, book_id=book.id, role="auditor")
    rows = db_session.query(UserBook).filter(UserBook.user_id == mama_user.id).all()
    assert len(rows) == 1
    assert rows[0].role == "auditor"


def test_list_books_api(client, auth_headers, db_session, book):
    """GET /api/books 返回当前用户可见账套列表。"""
    resp = client.get("/api/books", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data, list)
    assert any(b["id"] == book.id for b in data)


def test_list_books_member_only(client, db_session, book, mama_user):
    """成员制用户：未授权时列表为空；挂成员后可见对应账套。"""
    resp = client.post("/api/auth/login", json={"username": "mama", "password": "mama123456"})
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/api/books", headers=headers).json() == []

    book_service.add_member(db_session, user_id=mama_user.id, book_id=book.id, role="bookkeeper")
    books = client.get("/api/books", headers=headers).json()
    assert [b["id"] for b in books] == [book.id]


def test_create_book_requires_admin(client, book, mama_user):
    """非 admin 不能建账套。"""
    resp = client.post("/api/auth/login", json={"username": "mama", "password": "mama123456"})
    token = resp.json()["access_token"]
    resp = client.post(
        "/api/books",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "测试账套B", "start_period": "2026-09"},
    )
    assert resp.status_code == 403


def test_create_book_grants_membership(client, auth_headers, db_session, admin_user):
    """建账套后创建者自动成为该账套 admin 成员。"""
    resp = client.post(
        "/api/books",
        headers=auth_headers,
        json={"name": "测试账套C", "start_period": "2026-09", "taxpayer_type": "small_scale"},
    )
    assert resp.status_code == 201, resp.text
    book_id = resp.json()["id"]
    membership = db_session.query(UserBook).filter(
        UserBook.user_id == admin_user.id, UserBook.book_id == book_id
    ).first()
    assert membership is not None
    assert membership.role == "admin"


def test_book_access_denied_for_non_member(client, book, mama_user):
    """require_book_access：非成员访问账套详情 → 403。"""
    resp = client.post("/api/auth/login", json={"username": "mama", "password": "mama123456"})
    token = resp.json()["access_token"]
    resp = client.get(f"/api/books/{book.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert "无权访问" in resp.json()["detail"]


def test_book_access_granted_for_member(client, book, mama_user, db_session):
    """挂成员后同一接口放行。"""
    book_service.add_member(db_session, user_id=mama_user.id, book_id=book.id, role="bookkeeper")
    resp = client.post("/api/auth/login", json={"username": "mama", "password": "mama123456"})
    token = resp.json()["access_token"]
    resp = client.get(f"/api/books/{book.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["id"] == book.id
