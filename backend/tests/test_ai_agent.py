import json

from app.ledger import aux_service
from app.ledger.ai import agent as agent_mod
from app.models.ai import AIDoc


def _mock_chat_sequence(monkeypatch, responses):
    calls = []

    def fake_chat_with_tools(db, *, messages, tools, **kwargs):
        calls.append(list(messages))
        return responses[min(len(calls) - 1, len(responses) - 1)]

    monkeypatch.setattr(agent_mod, "chat_with_tools", fake_chat_with_tools)
    return calls


def _text_doc(db_session, book, note="阿里云服务器费 8480"):
    doc = AIDoc(book_id=book.id, doc_type="text", source_kind="text", fields_json=json.dumps({"note": note}), status="parsed")
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    return doc


VOUCHER_JSON = json.dumps({
    "reply": "已生成凭证草稿，请确认",
    "voucher": {
        "voucher_date": "2026-08-05",
        "lines": [
            {"summary": "云服务器费", "account_code": "5401", "debit": "8396.04", "credit": "0"},
            {"summary": "税额", "account_code": "2221", "debit": "83.96", "credit": "0"},
            {"summary": "银行付款", "account_code": "1002", "debit": "0", "credit": "8480.00"},
        ],
    },
}, ensure_ascii=False)


def test_agent_tool_then_voucher(db_session, book, monkeypatch, client, auth_headers):
    doc = _text_doc(db_session, book)
    _mock_chat_sequence(monkeypatch, [
        {"content": "", "tool_calls": [
            {"id": "c1", "name": "find_or_create_contact", "arguments": {"name": "阿里云计算", "ctype": "supplier"}}
        ], "usage": {}},
        {"content": VOUCHER_JSON, "tool_calls": [], "usage": {}},
    ])

    resp = client.post("/api/ai/chat", headers=auth_headers, json={
        "book_id": book.id, "doc_id": doc.id, "history": [{"role": "user", "content": "阿里云8480记一下"}],
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["voucher_saved"] is True
    assert data["voucher"]["lines"][0]["debit"] == "8396.04"
    assert data["trace"][0]["tool"] == "find_or_create_contact"
    assert data["stopped_by_ask_user"] is False

    db_session.expire_all()
    refreshed = db_session.get(AIDoc, doc.id)
    assert refreshed.status == "suggested"

    confirm = client.post("/api/ai/confirm", headers=auth_headers, json={
        "doc_id": doc.id, "voucher_date": "2026-08-05", "lines": data["voucher"]["lines"],
    })
    assert confirm.status_code == 201, confirm.text
    assert confirm.json()["source"] == "ai"


def test_agent_ask_user_stops_loop(db_session, book, monkeypatch, client, auth_headers):
    doc = _text_doc(db_session, book, note="一笔餐费")
    _mock_chat_sequence(monkeypatch, [
        {"content": "", "tool_calls": [
            {"id": "c1", "name": "ask_user", "arguments": {"question": "这笔餐费是请客户，还是员工聚餐？"}}
        ], "usage": {}},
    ])

    resp = client.post("/api/ai/chat", headers=auth_headers, json={"book_id": book.id, "doc_id": doc.id})
    assert resp.status_code == 200
    data = resp.json()
    assert data["stopped_by_ask_user"] is True
    assert "客户" in data["reply"]
    assert data["voucher"] is None
    db_session.expire_all()
    assert db_session.get(AIDoc, doc.id).status == "parsed"


def test_tools_execute_against_real_data(db_session, book, mama_user, auditor_user, post_flow, contacts_pair):
    from app.ledger.ai.agent import execute_tool, extract_json
    from app.ledger.voucher_service import create_voucher

    contact = contacts_pair["customer"]
    voucher = create_voucher(
        db_session, book_id=book.id, voucher_date="2026-08-06",
        lines=[
            {"summary": "应收", "account_code": "1122", "debit": "11300.00", "credit": "0", "contact_id": contact.id},
            {"summary": "收入", "account_code": "5001", "debit": "0", "credit": "11188.12"},
            {"summary": "税", "account_code": "2221", "debit": "0", "credit": "111.88"},
        ],
        operator_id=mama_user.id, attachment_count=1,
    )
    post_flow(voucher, mama_user, auditor_user)

    invoices = json.loads(execute_tool(db_session, book.id, "search_invoices", {}))
    assert isinstance(invoices, list)

    balance = json.loads(execute_tool(db_session, book.id, "query_balance", {"period": "2026-08", "account_code": "1122"}))
    match = [r for r in balance if r["account_code"] == "1122"]
    assert match and match[0]["closing_debit"] == "11300.00"

    vouchers = json.loads(execute_tool(db_session, book.id, "query_vouchers", {"period": "2026-08", "status": "posted"}))
    assert any(v["voucher_id"] == voucher.id for v in vouchers)

    listing = json.loads(execute_tool(db_session, book.id, "list_contacts", {"ctype": "customer"}))
    assert any(c["contact_id"] == contact.id for c in listing)

    created = json.loads(execute_tool(db_session, book.id, "find_or_create_contact", {"name": "测试客户", "ctype": "customer"}))
    assert created["created"] is False and created["contact_id"] == contact.id

    unknown = json.loads(execute_tool(db_session, book.id, "no_such_tool", {}))
    assert "error" in unknown

    assert extract_json("前置说明 {\"a\": 1} 后缀") == {"a": 1}
    assert extract_json("没有 JSON") is None


def test_chat_requires_doc_belongs_to_book(client, auth_headers, db_session, book):
    other = AIDoc(book_id=book.id, doc_type="text", source_kind="text", fields_json="{}", status="confirmed")
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)
    resp = client.post("/api/ai/chat", headers=auth_headers, json={"book_id": book.id, "doc_id": other.id})
    assert resp.status_code == 400
    assert "已确认落账" in resp.json()["detail"]


def test_search_contacts_and_mismatch_guard(db_session, book, contacts_pair):
    """先查后建流程：search_contacts 模糊匹配；find_or_create_contact 类型不匹配时返回警示。"""
    from app.ledger.ai.agent import execute_tool

    # 模糊查询：关键字是档案名的一部分
    hits = json.loads(execute_tool(db_session, book.id, "search_contacts", {"keyword": "测试客户"}))
    assert any(c["contact_id"] == contacts_pair["customer"].id for c in hits)

    # 反向包含：档案名是关键字的一部分（用户说了更长的名字）
    hits = json.loads(execute_tool(db_session, book.id, "search_contacts", {"keyword": "上海测试客户有限公司"}))
    assert any(c["contact_id"] == contacts_pair["customer"].id for c in hits)

    # 类型过滤
    hits = json.loads(execute_tool(db_session, book.id, "search_contacts", {"keyword": "测试", "ctype": "supplier"}))
    assert all(c["ctype"] == "supplier" for c in hits) and hits

    # 查不到
    assert json.loads(execute_tool(db_session, book.id, "search_contacts", {"keyword": "不存在的单位"})) == []

    # 同名但类型不一致 → 返回警示标记，不能静默用错
    r = json.loads(execute_tool(db_session, book.id, "find_or_create_contact", {"name": "测试客户", "ctype": "supplier"}))
    assert r["created"] is False and r["ctype_mismatch"] is True
    assert "不一致" in r["note"]

    # 同名同类型 → 正常复用
    r = json.loads(execute_tool(db_session, book.id, "find_or_create_contact", {"name": "测试客户", "ctype": "customer"}))
    assert r["created"] is False and r["contact_id"] == contacts_pair["customer"].id and "ctype_mismatch" not in r


def test_seed_default_aux_still_on(db_session, book):
    account = aux_service.set_aux_types.__self__ if hasattr(aux_service.set_aux_types, "__self__") else None
    from sqlalchemy import select
    from app.models.account import Account

    row = db_session.scalar(select(Account).where(Account.book_id == book.id, Account.code == "1122"))
    assert row.aux_types == "contact:customer"


# ---------- G3 往来 ID 净化（_sanitize_contacts 硬兜底）----------


def test_sanitize_contacts_clears_fabricated_id(db_session, book):
    """模型编造不存在的 contact_id → 落库前置空，返回清理计数。"""
    from app.ledger.ai.agent import _sanitize_contacts

    voucher = {"voucher_date": "2026-08-05", "lines": [
        {"summary": "应收", "account_code": "1122", "debit": "100.00", "credit": "0", "contact_id": 99999},
        {"summary": "收入", "account_code": "5001", "debit": "0", "credit": "100.00"},
    ]}
    cleaned = _sanitize_contacts(db_session, book.id, voucher)
    assert cleaned == 1
    assert voucher["lines"][0]["contact_id"] is None


def test_sanitize_contacts_cross_book_rejected_valid_kept(db_session, book, contacts_pair):
    """跨账套 ID 置空（数据隔离）；本账套有效 ID 保留。"""
    from app.ledger.book_service import create_book
    from app.ledger.ai.agent import _sanitize_contacts

    other_book = create_book(
        db_session, name="另一家测试公司", tax_no="91310000MA1K35X00B", start_period="2026-08"
    )
    stranger = aux_service.create_contact(
        db_session, book_id=other_book.id, name="别家的客户", ctype="customer"
    )

    voucher = {"voucher_date": "2026-08-05", "lines": [
        {"summary": "应收", "account_code": "1122", "debit": "100.00", "credit": "0", "contact_id": stranger.id},
        {"summary": "应收2", "account_code": "1122", "debit": "50.00", "credit": "0", "contact_id": contacts_pair["customer"].id},
        {"summary": "收入", "account_code": "5001", "debit": "0", "credit": "150.00"},
    ]}
    cleaned = _sanitize_contacts(db_session, book.id, voucher)
    assert cleaned == 1
    assert voucher["lines"][0]["contact_id"] is None       # 跨账套 → 置空
    assert voucher["lines"][1]["contact_id"] == contacts_pair["customer"].id  # 本账套有效 → 保留
