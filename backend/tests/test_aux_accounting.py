from decimal import Decimal

from app.ledger import aux_service
from app.ledger.exceptions import AccountError, BookError, VoucherError
from app.ledger.voucher_service import create_voucher


def _enable_aux(db_session, book, code="1122"):
    return aux_service.set_aux_types(db_session, book_id=book.id, code=code, aux_types=["contact"])


def _make_contact(db_session, book, name="杭州智算科技", ctype="customer"):
    return aux_service.create_contact(db_session, book_id=book.id, name=name, ctype=ctype)


def test_create_contact_and_unique_name(db_session, book):
    contact = _make_contact(db_session, book)
    assert contact.id > 0
    assert contact.ctype == "customer"
    try:
        _make_contact(db_session, book)
        raise AssertionError("重复名称未拒绝")
    except BookError:
        pass


def test_set_aux_types_validates(db_session, book):
    account = _enable_aux(db_session, book)
    assert account.aux_types == "contact"
    try:
        aux_service.set_aux_types(db_session, book_id=book.id, code="1002", aux_types=["project"])
        raise AssertionError("未知辅助类型未拒绝")
    except AccountError:
        pass


def test_voucher_requires_contact_on_aux_account(db_session, book, mama_user):
    contact = _make_contact(db_session, book)
    _enable_aux(db_session, book)
    try:
        create_voucher(
            db_session, book_id=book.id, voucher_date="2026-08-06",
            lines=[
                {"summary": "应收", "account_code": "1122", "debit": "11300.00", "credit": "0"},
                {"summary": "收入", "account_code": "5001", "debit": "0", "credit": "11188.12"},
                {"summary": "税", "account_code": "2221", "debit": "0", "credit": "111.88"},
            ],
            operator_id=mama_user.id, attachment_count=1,
        )
        raise AssertionError("缺少往来单位未拒绝")
    except VoucherError as exc:
        assert "往来单位" in str(exc)

    voucher = create_voucher(
        db_session, book_id=book.id, voucher_date="2026-08-06",
        lines=[
            {"summary": "应收", "account_code": "1122", "debit": "11300.00", "credit": "0", "contact_id": contact.id},
            {"summary": "收入", "account_code": "5001", "debit": "0", "credit": "11188.12"},
            {"summary": "税", "account_code": "2221", "debit": "0", "credit": "111.88"},
        ],
        operator_id=mama_user.id, attachment_count=1,
    )
    assert voucher.lines[0].contact_id == contact.id


def test_aux_trial_balance_aggregates_by_contact(db_session, book, mama_user, auditor_user, post_flow):
    contact_a = _make_contact(db_session, book, name="杭州智算科技")
    contact_b = _make_contact(db_session, book, name="上海云途数据")
    _enable_aux(db_session, book)

    for contact, amount in [(contact_a, "21200.00"), (contact_b, "11300.00"), (contact_a, "5000.00")]:
        lines = [
            {"summary": "应收", "account_code": "1122", "debit": amount, "credit": "0", "contact_id": contact.id},
            {"summary": "收入", "account_code": "5001", "debit": "0", "credit": amount},
        ]
        voucher = create_voucher(
            db_session, book_id=book.id, voucher_date="2026-08-06",
            lines=lines, operator_id=mama_user.id, attachment_count=1,
        )
        post_flow(voucher, mama_user, auditor_user)

    report = aux_service.aux_trial_balance(
        db_session, book_id=book.id, period="2026-08", account_code="1122"
    )
    rows = {row["contact_name"]: row for row in report["rows"]}
    assert rows["杭州智算科技"]["closing_debit"] == "26200.00"
    assert rows["上海云途数据"]["closing_debit"] == "11300.00"

    try:
        aux_service.aux_trial_balance(db_session, book_id=book.id, period="2026-08", account_code="5602")
        raise AssertionError("未启用辅助核算的科目未拒绝")
    except AccountError:
        pass


def test_complete_trial_balance_includes_rollups(db_session, book, mama_user, auditor_user, post_flow):
    from app.ledger.balances import trial_balance

    voucher = create_voucher(
        db_session, book_id=book.id, voucher_date="2026-08-06",
        lines=[
            {"summary": "出资", "account_code": "1002", "debit": "500000.00", "credit": "0"},
            {"summary": "实收资本", "account_code": "3001", "debit": "0", "credit": "500000.00"},
        ],
        operator_id=mama_user.id, attachment_count=1,
    )
    post_flow(voucher, mama_user, auditor_user)

    simple = trial_balance(db_session, book_id=book.id, period="2026-08")
    complete = trial_balance(db_session, book_id=book.id, period="2026-08", complete=True)

    assert len(complete["rows"]) > len(simple["rows"])
    rollups = [row for row in complete["rows"] if row.get("is_rollup")]
    bank_rollup = [row for row in rollups if row["account_code"] == "1002"][0]
    assert bank_rollup["closing_debit"] == "500000.00"

    capital_rollup = [row for row in rollups if row["account_code"] == "3001"][0]
    assert capital_rollup["closing_credit"] == "500000.00"

    assert complete["is_balanced"] is True
    assert complete["totals"] == simple["totals"]

    codes = [row["account_code"] for row in complete["rows"]]
    assert codes.index("1002") < codes.index("1001") or codes.index("1001") < codes.index("1002")
    first_level1_idx = codes.index("1001")
    assert complete["rows"][first_level1_idx].get("is_rollup") is True


def test_contact_crud_api(client, auth_headers, book):
    created = client.post(
        "/api/contacts",
        headers=auth_headers,
        params={"book_id": book.id, "name": "北京数澜科技", "ctype": "supplier"},
    )
    assert created.status_code == 201, created.text

    listing = client.get("/api/contacts", headers=auth_headers, params={"book_id": book.id}).json()
    assert len(listing) == 1
    assert listing[0]["ctype"] == "supplier"

    resp = client.patch(
        f"/api/contacts/{listing[0]['id']}",
        headers=auth_headers,
        params={"is_active": False},
    )
    assert resp.json()["is_active"] is False


def test_patch_account_aux_api(client, auth_headers, book):
    resp = client.patch(
        "/api/accounts/1122/aux",
        headers=auth_headers,
        params={"book_id": book.id, "aux_types": "contact"},
    )
    assert resp.status_code == 200
    assert resp.json()["aux_types"] == ["contact"]

    resp = client.patch(
        "/api/accounts/1122/aux",
        headers=auth_headers,
        params={"book_id": book.id, "aux_types": ""},
    )
    assert resp.json()["aux_types"] == []


def test_aux_balance_api(client, auth_headers, db_session, book, mama_user, auditor_user, post_flow):
    contact = _make_contact(db_session, book)
    _enable_aux(db_session, book)
    voucher = create_voucher(
        db_session, book_id=book.id, voucher_date="2026-08-06",
        lines=[
            {"summary": "应收", "account_code": "1122", "debit": "9000.00", "credit": "0", "contact_id": contact.id},
            {"summary": "收入", "account_code": "5001", "debit": "0", "credit": "9000.00"},
        ],
        operator_id=mama_user.id, attachment_count=1,
    )
    post_flow(voucher, mama_user, auditor_user)

    resp = client.get(
        "/api/reports/aux-balance",
        headers=auth_headers,
        params={"book_id": book.id, "period": "2026-08", "account_code": "1122"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["rows"][0]["contact_name"] == "杭州智算科技"
    assert data["rows"][0]["closing_debit"] == "9000.00"


def test_complete_trial_balance_api(client, auth_headers, db_session, book, mama_user, auditor_user, post_flow):
    voucher = create_voucher(
        db_session, book_id=book.id, voucher_date="2026-08-06",
        lines=[
            {"summary": "出资", "account_code": "1002", "debit": "100000.00", "credit": "0"},
            {"summary": "实收资本", "account_code": "3001", "debit": "0", "credit": "100000.00"},
        ],
        operator_id=mama_user.id, attachment_count=1,
    )
    post_flow(voucher, mama_user, auditor_user)
    resp = client.get(
        "/api/reports/trial-balance",
        headers=auth_headers,
        params={"book_id": book.id, "period": "2026-08", "complete": True},
    )
    rows = resp.json()["rows"]
    assert any(row.get("is_rollup") for row in rows)
