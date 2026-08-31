from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import hash_password
from app.ledger import voucher_service
from app.main import app
from app.models.user import User
from app.models.voucher import Voucher, VoucherLine


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def db_session(engine):
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    yield session
    session.close()


@pytest.fixture()
def client(engine, db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def admin_user(db_session):
    user = User(
        username="admin",
        password_hash=hash_password("admin123"),
        display_name="管理员",
        role="admin",
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture()
def mama_user(db_session):
    user = User(
        username="mama",
        password_hash=hash_password("mama123456"),
        display_name="妈妈",
        role="bookkeeper",
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture()
def auditor_user(db_session):
    user = User(
        username="papa",
        password_hash=hash_password("papa123456"),
        display_name="爸爸",
        role="auditor",
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture()
def book(db_session):
    from app.ledger import book_service

    return book_service.create_book(
        db_session,
        name="测试科技有限公司",
        tax_no="91310000MA1K35X00A",
        start_period="2026-08",
    )


@pytest.fixture()
def auth_headers(client, admin_user):
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def post_flow(db_session):
    def _run(voucher, preparer, auditor):
        voucher_service.submit_voucher(db_session, voucher_id=voucher.id, operator_id=preparer.id)
        voucher_service.audit_voucher(db_session, voucher_id=voucher.id, operator=auditor)
        voucher_service.post_voucher(db_session, voucher_id=voucher.id, operator=auditor)
        return voucher

    return _run


@pytest.fixture()
def mock_month(db_session, book, mama_user, auditor_user, post_flow):
    from app.ledger.mock_data import setup_detail_accounts, voucher_payloads

    setup_detail_accounts(db_session, book.id)
    vouchers = []
    for payload in voucher_payloads():
        voucher = voucher_service.create_voucher(
            db_session, book_id=book.id, operator_id=mama_user.id, **payload
        )
        post_flow(voucher, mama_user, auditor_user)
        vouchers.append(voucher)
    return vouchers


@pytest.fixture()
def mock_month_with_carryover(db_session, mock_month, book, mama_user, auditor_user, post_flow):
    from app.ledger import close_service

    drafts = close_service.generate_carryover(
        db_session, book_id=book.id, period="2026-08", operator_id=mama_user.id
    )
    for draft in drafts:
        post_flow(draft, mama_user, auditor_user)
    return {"vouchers": mock_month, "carryovers": drafts}


@pytest.fixture()
def attachments_dir(tmp_path, monkeypatch):
    from app.core.config import get_settings

    target = tmp_path / "attachments"
    monkeypatch.setattr(get_settings(), "ATTACHMENTS_DIR", str(target))
    return target


@pytest.fixture()
def get_posted_nets(db_session):
    def _run(book_id: int) -> dict[str, Decimal]:
        rows = db_session.execute(
            select(VoucherLine.account_code, VoucherLine.debit, VoucherLine.credit)
            .join(Voucher, VoucherLine.voucher_id == Voucher.id)
            .where(Voucher.book_id == book_id, Voucher.status.in_(["posted", "voided"]))
        ).all()
        nets: dict[str, Decimal] = {}
        for code, debit, credit in rows:
            nets[code] = nets.get(code, Decimal("0")) + Decimal(str(debit)) - Decimal(str(credit))
        return {code: net for code, net in nets.items() if net != 0}

    return _run
