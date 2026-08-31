from app.models.ai import AIDoc


def _upload_xml(client, auth_headers, book):
    from tests.mock_files import xml_invoice_bytes

    return client.post(
        "/api/ai/parse",
        headers=auth_headers,
        params={"book_id": book.id},
        files={"file": ("invoice.xml", xml_invoice_bytes(), "application/xml")},
    )


def test_parse_requires_auth_or_input(client, book):
    assert client.post("/api/ai/parse", params={"book_id": book.id}).status_code == 401


def test_parse_without_file_and_note_rejected(client, auth_headers, book):
    resp = client.post("/api/ai/parse", headers=auth_headers, params={"book_id": book.id})
    assert resp.status_code == 400


def test_parse_and_suggest_and_confirm_full_flow(
    client, db_session, book, mama_user, auth_headers, attachments_dir, monkeypatch, contacts_pair
):
    parse_resp = _upload_xml(client, auth_headers, book)
    assert parse_resp.status_code == 200, parse_resp.text
    parse_data = parse_resp.json()
    doc_id = parse_data["doc_id"]
    assert parse_data["fields"]["invoice_no"] == "25317000000123456701"

    model_json = (
        '{"voucher_date":"2026-08-05","lines":['
        '{"summary":"收到云服务费","account_code":"1122","debit":"11300.00","credit":"0.00"},'
        '{"summary":"确认收入","account_code":"5001","debit":"0.00","credit":"11188.12"},'
        '{"summary":"计提增值税","account_code":"2221","debit":"0.00","credit":"111.88"}]}'
    )
    monkeypatch.setattr(
        "app.ledger.ai.suggest.gateway.chat",
        lambda db, **kwargs: {"content": model_json, "usage": {}},
    )
    suggest_resp = client.post(
        "/api/ai/suggest",
        headers=auth_headers,
        json={"book_id": book.id, "doc_id": doc_id},
    )
    assert suggest_resp.status_code == 200, suggest_resp.text
    suggestion = suggest_resp.json()
    assert suggestion["confidence"] == 0.9
    assert suggestion["voucher"]["lines"][0]["debit"] == "11300.00"

    confirm_lines = suggestion["voucher"]["lines"]
    confirm_lines[0]["contact_id"] = contacts_pair["customer"].id
    confirm_resp = client.post(
        "/api/ai/confirm",
        headers=auth_headers,
        json={
            "doc_id": doc_id,
            "voucher_date": "2026-08-05",
            "lines": confirm_lines,
        },
    )
    assert confirm_resp.status_code == 201, confirm_resp.text
    voucher = confirm_resp.json()
    assert voucher["source"] == "ai"
    assert voucher["attachment_count"] == 1
    assert voucher["lines"][0]["contact_id"] == contacts_pair["customer"].id

    doc = db_session.get(AIDoc, doc_id)
    assert doc.status == "confirmed"
    assert doc.voucher_id == voucher["id"]

    files = [f for f in attachments_dir.rglob("*") if f.is_file()]
    assert len(files) == 1

    docs = client.get(
        "/api/ai/documents", headers=auth_headers, params={"book_id": book.id}
    ).json()
    assert docs[0]["id"] == doc_id
    assert docs[0]["status"] == "confirmed"


def test_confirm_twice_rejected(client, auth_headers, book, mama_user, monkeypatch, db_session):
    parse_resp = _upload_xml(client, auth_headers, book)
    doc_id = parse_resp.json()["doc_id"]
    monkeypatch.setattr(
        "app.ledger.ai.suggest.gateway.chat",
        lambda db, **kwargs: {
            "content": '{"voucher_date":"2026-08-05","lines":[{"summary":"a","account_code":"1002","debit":"10.00","credit":"0.00"},{"summary":"b","account_code":"5603","debit":"0.00","credit":"10.00"}]}',
            "usage": {},
        },
    )
    client.post("/api/ai/suggest", headers=auth_headers, json={"book_id": book.id, "doc_id": doc_id})
    body = {
        "doc_id": doc_id,
        "voucher_date": "2026-08-05",
        "lines": [
            {"summary": "a", "account_code": "1002", "debit": "10.00", "credit": "0"},
            {"summary": "b", "account_code": "5603", "debit": "0", "credit": "10.00"},
        ],
    }
    assert client.post("/api/ai/confirm", headers=auth_headers, json=body).status_code == 201
    assert client.post("/api/ai/confirm", headers=auth_headers, json=body).status_code == 400


def test_discard_document(client, auth_headers, book):
    parse_resp = _upload_xml(client, auth_headers, book)
    doc_id = parse_resp.json()["doc_id"]
    resp = client.post(f"/api/ai/documents/{doc_id}/discard", headers=auth_headers)
    assert resp.status_code == 200
    docs = client.get(
        "/api/ai/documents", headers=auth_headers, params={"book_id": book.id, "status": "discarded"}
    ).json()
    assert docs[0]["status"] == "discarded"


def test_text_note_flow(client, auth_headers, book, monkeypatch):
    parse_resp = client.post(
        "/api/ai/parse",
        headers=auth_headers,
        params={"book_id": book.id},
        data={"note": "今天给阿里云付了3200元服务器费"},
    )
    assert parse_resp.status_code == 200
    doc_id = parse_resp.json()["doc_id"]
    assert parse_resp.json()["doc_type"] == "text"

    monkeypatch.setattr(
        "app.ledger.ai.suggest.gateway.chat",
        lambda db, **kwargs: {
            "content": '{"voucher_date":"2026-08-31","lines":[{"summary":"阿里云服务器费","account_code":"5401","debit":"3200.00","credit":"0.00"},{"summary":"银行付款","account_code":"1002","debit":"0.00","credit":"3200.00"}]}',
            "usage": {},
        },
    )
    suggest_resp = client.post(
        "/api/ai/suggest",
        headers=auth_headers,
        json={"book_id": book.id, "doc_id": doc_id},
    )
    assert suggest_resp.status_code == 200
    assert suggest_resp.json()["voucher"]["lines"][0]["debit"] == "3200.00"
