from decimal import Decimal

from app.ledger import aux_service
from app.ledger.exceptions import AccountError, BookError, VoucherError
from app.ledger.voucher_service import create_voucher


def _enable_customer_aux(db_session, book, code="1122"):
    return aux_service.set_aux_types(db_session, book_id=book.id, code=code, aux_types=["contact:customer"])


def _make_contact(db_session, book, name="杭州智算科技", ctype="customer"):
    return aux_service.create_contact(db_session, book_id=book.id, name=name, ctype=ctype)


def test_seed_defaults_on_create_book(db_session, book):
    from sqlalchemy import select

    from app.models.account import Account

    cases = {
        "1122": ["contact:customer"],
        "1121": ["contact:customer"],
        "2202": ["contact:supplier"],
        "2203": ["contact:customer"],
        "1123": ["contact:supplier"],
        "1221": ["contact:employee", "contact:other"],
        "2241": ["contact:employee", "contact:other"],
    }
    for code, expected in cases.items():
        row = db_session.scalar(
            select(Account).where(Account.book_id == book.id, Account.code == code)
        )
        assert row.aux_types.split(",") == expected, f"{code}: {row.aux_types}"


def test_create_contact_four_types_and_unique_name(db_session, book):
    for name, ctype in [
        ("杭州智算科技", "customer"),
        ("上海云途数据", "supplier"),
        ("张会计", "employee"),
        ("/misc 往来", "other"),
    ]:
        contact = aux_service.create_contact(db_session, book_id=book.id, name=name, ctype=ctype)
        assert contact.ctype == ctype
    try:
        aux_service.create_contact(db_session, book_id=book.id, name="杭州智算科技", ctype="customer")
        raise AssertionError("重复名称未拒绝")
    except BookError:
        pass
    try:
        aux_service.create_contact(db_session, book_id=book.id, name="x单位", ctype="unknown")
        raise AssertionError("未知类型未拒绝")
    except BookError:
        pass


def test_set_aux_types_typed_format(db_session, book):
    account, _ = _enable_customer_aux(db_session, book)
    assert account.aux_types == "contact:customer"
    try:
        aux_service.set_aux_types(db_session, book_id=book.id, code="1002", aux_types=["contact:project"])
        raise AssertionError("非法类型未拒绝")
    except AccountError:
        pass
    normalized = aux_service.normalize_aux_types("contact")
    assert normalized == ["contact:customer", "contact:supplier"]


def test_voucher_type_matching(db_session, book, mama_user):
    customer = _make_contact(db_session, book, name="客户A", ctype="customer")
    supplier = _make_contact(db_session, book, name="供应商B", ctype="supplier")
    employee = _make_contact(db_session, book, name="张会计", ctype="employee")
    _enable_customer_aux(db_session, book)

    try:
        create_voucher(
            db_session, book_id=book.id, voucher_date="2026-08-06",
            lines=[
                {"summary": "应收", "account_code": "1122", "debit": "11300.00", "credit": "0", "contact_id": supplier.id},
                {"summary": "收入", "account_code": "5001", "debit": "0", "credit": "11300.00"},
            ],
            operator_id=mama_user.id, attachment_count=1,
        )
        raise AssertionError("客户科目选供应商未拒绝")
    except VoucherError as exc:
        assert "仅允许" in str(exc)

    voucher = create_voucher(
        db_session, book_id=book.id, voucher_date="2026-08-06",
        lines=[
            {"summary": "应收", "account_code": "1122", "debit": "11300.00", "credit": "0", "contact_id": customer.id},
            {"summary": "收入", "account_code": "5001", "debit": "0", "credit": "11188.12"},
            {"summary": "税", "account_code": "2221", "debit": "0", "credit": "111.88"},
        ],
        operator_id=mama_user.id, attachment_count=1,
    )
    assert voucher.lines[0].contact_id == customer.id

    aux_service.set_aux_types(
        db_session, book_id=book.id, code="1221",
        aux_types=["contact:employee", "contact:other"],
    )
    employee_voucher = create_voucher(
        db_session, book_id=book.id, voucher_date="2026-08-07",
        lines=[
            {"summary": "备用金", "account_code": "1221", "debit": "2000.00", "credit": "0", "contact_id": employee.id},
            {"summary": "现金", "account_code": "1001", "debit": "0", "credit": "2000.00"},
        ],
        operator_id=mama_user.id, attachment_count=1,
    )
    assert employee_voucher.lines[0].contact_id == employee.id


def test_aux_trial_balance_aggregates_by_contact(db_session, book, mama_user, auditor_user, post_flow):
    contact_a = _make_contact(db_session, book, name="杭州智算科技")
    contact_b = _make_contact(db_session, book, name="上海云途数据")
    _enable_customer_aux(db_session, book)

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

    filtered = aux_service.aux_trial_balance(
        db_session, book_id=book.id, period="2026-08", account_code="1122", ctype="supplier"
    )
    assert filtered["rows"] == []

    try:
        aux_service.aux_trial_balance(db_session, book_id=book.id, period="2026-08", account_code="5602")
        raise AssertionError("未启用辅助核算的科目未拒绝")
    except AccountError:
        pass


def test_set_aux_types_warns_on_usage(db_session, book, mama_user, auditor_user, post_flow):
    contact = _make_contact(db_session, book)
    _enable_customer_aux(db_session, book)
    voucher = create_voucher(
        db_session, book_id=book.id, voucher_date="2026-08-06",
        lines=[
            {"summary": "应收", "account_code": "1122", "debit": "9000.00", "credit": "0", "contact_id": contact.id},
            {"summary": "收入", "account_code": "5001", "debit": "0", "credit": "9000.00"},
        ],
        operator_id=mama_user.id, attachment_count=1,
    )
    post_flow(voucher, mama_user, auditor_user)

    account, warned = aux_service.set_aux_types(
        db_session, book_id=book.id, code="1122",
        aux_types=["contact:customer", "contact:other"],
    )
    assert warned is True
    assert account.aux_types == "contact:customer,contact:other"

    fresh = aux_service.set_aux_types(
        db_session, book_id=book.id, code="2202", aux_types=["contact:supplier"]
    )
    assert fresh[1] is False


def test_contact_used_by_labels(db_session, book, mama_user, auditor_user, post_flow):
    contact = _make_contact(db_session, book)
    _enable_customer_aux(db_session, book)
    voucher = create_voucher(
        db_session, book_id=book.id, voucher_date="2026-08-06",
        lines=[
            {"summary": "应收", "account_code": "1122", "debit": "1000.00", "credit": "0", "contact_id": contact.id},
            {"summary": "收入", "account_code": "5001", "debit": "0", "credit": "1000.00"},
        ],
        operator_id=mama_user.id, attachment_count=1,
    )
    post_flow(voucher, mama_user, auditor_user)
    labels = aux_service.contact_used_by_labels(db_session, book.id, contact.id)
    assert labels == ["1122 应收账款"]


def test_contact_api_with_used_by(client, auth_headers, db_session, book, mama_user, auditor_user, post_flow):
    contact = _make_contact(db_session, book)
    _enable_customer_aux(db_session, book)
    voucher = create_voucher(
        db_session, book_id=book.id, voucher_date="2026-08-06",
        lines=[
            {"summary": "应收", "account_code": "1122", "debit": "1000.00", "credit": "0", "contact_id": contact.id},
            {"summary": "收入", "account_code": "5001", "debit": "0", "credit": "1000.00"},
        ],
        operator_id=mama_user.id, attachment_count=1,
    )
    post_flow(voucher, mama_user, auditor_user)

    listing = client.get("/api/contacts", headers=auth_headers, params={"book_id": book.id}).json()
    assert listing[0]["used_by"] == ["1122 应收账款"]

    customer_only = client.get(
        "/api/contacts", headers=auth_headers, params={"book_id": book.id, "ctype": "customer"}
    ).json()
    assert len(customer_only) == 1


def test_patch_account_aux_api_warns(client, auth_headers, db_session, book, mama_user, auditor_user, post_flow):
    contact = _make_contact(db_session, book)
    _enable_customer_aux(db_session, book)
    voucher = create_voucher(
        db_session, book_id=book.id, voucher_date="2026-08-06",
        lines=[
            {"summary": "应收", "account_code": "1122", "debit": "1000.00", "credit": "0", "contact_id": contact.id},
            {"summary": "收入", "account_code": "5001", "debit": "0", "credit": "1000.00"},
        ],
        operator_id=mama_user.id, attachment_count=1,
    )
    post_flow(voucher, mama_user, auditor_user)

    resp = client.patch(
        "/api/accounts/1122/aux",
        headers=auth_headers,
        params={"book_id": book.id, "aux_types": "contact:customer,contact:other"},
    )
    assert resp.status_code == 200
    assert "发生额" in resp.json()["warning"]
    assert resp.json()["aux_types"] == ["contact:customer", "contact:other"]
