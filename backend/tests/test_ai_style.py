import json

from app.ledger.ai.agent import _style_segment
from app.ledger.ai.packs import TAGS
from app.models.ai import AiStyleSetting


def _set_style(db_session, book, tags, desc=""):
    db_session.add(AiStyleSetting(book_id=book.id, tags_json=json.dumps(tags), business_desc=desc))
    db_session.commit()


def test_style_segment_unconfigured_returns_empty(db_session, book):
    """未配置账套：风格段为空串——agent 行为与历史版本完全一致（回归护栏）。"""
    assert _style_segment(db_session, book.id) == ""


def test_style_segment_empty_setting_returns_empty(db_session, book):
    _set_style(db_session, book, [], "")
    assert _style_segment(db_session, book.id) == ""


def test_style_segment_tags_and_desc(db_session, book):
    _set_style(db_session, book, ["software", "ecommerce"], "我们做 SaaS，主营记账工具")
    segment = _style_segment(db_session, book.id)
    # 护栏头必在（用户配置永远不能凌驾核心规则）
    assert "不得改变、凌驾" in segment
    # 两个标签的片段都注入
    assert TAGS["software"]["prompt"].splitlines()[0] in segment
    assert TAGS["ecommerce"]["prompt"].splitlines()[0] in segment
    assert "业务描述：我们做 SaaS，主营记账工具" in segment


def test_style_segment_invalid_tags_dropped(db_session, book):
    """标签库演进后，存量配置里的已下线标签静默丢弃，合法标签仍生效。"""
    _set_style(db_session, book, ["software", "no_longer_exists"], "x")
    segment = _style_segment(db_session, book.id)
    assert TAGS["software"]["prompt"].splitlines()[0] in segment
    assert "no_longer_exists" not in segment


def test_style_segment_broken_json_tolerated(db_session, book):
    _set_style(db_session, book, [], "仅描述")
    db_session.query(AiStyleSetting).filter(AiStyleSetting.book_id == book.id).update({"tags_json": "{bad json"})
    db_session.commit()
    segment = _style_segment(db_session, book.id)
    assert "业务描述：仅描述" in segment


def test_get_ai_style_library_and_current(client, auth_headers, book):
    resp = client.get(f"/api/books/{book.id}/ai-style", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert {t["id"] for t in data["tags"]} == set(TAGS.keys())
    assert data["current"] == {"tags": [], "business_desc": ""}


def test_put_ai_style_requires_admin(client, book, db_session, mama_user):
    resp = client.post("/api/auth/login", json={"username": "mama", "password": "mama123456"})
    token = resp.json()["access_token"]
    resp = client.put(
        f"/api/books/{book.id}/ai-style",
        headers={"Authorization": f"Bearer {token}"},
        json={"tags": ["software"], "business_desc": "x"},
    )
    assert resp.status_code == 403


def test_put_ai_style_validates(client, auth_headers, book):
    resp = client.put(
        f"/api/books/{book.id}/ai-style",
        headers=auth_headers,
        json={"tags": ["not_a_tag"], "business_desc": ""},
    )
    assert resp.status_code == 400
    assert "未知行业标签" in resp.json()["detail"]


def test_put_ai_style_roundtrip_and_agent_prompt(client, auth_headers, db_session, book, monkeypatch):
    """保存 → 读回一致；且 agent 的 system prompt 含风格段（端到端注入）。"""
    from app.ledger.ai import agent as agent_mod

    resp = client.put(
        f"/api/books/{book.id}/ai-style",
        headers=auth_headers,
        json={"tags": ["software"], "business_desc": "我们做 SaaS 记账工具"},
    )
    assert resp.status_code == 200, resp.text

    got = client.get(f"/api/books/{book.id}/ai-style", headers=auth_headers).json()
    assert got["current"]["tags"] == ["software"]
    assert got["current"]["business_desc"] == "我们做 SaaS 记账工具"

    captured = []

    def fake_chat(db, *, messages, tools, **kwargs):
        captured.append(list(messages))
        return {"content": json.dumps({"reply": "好的", "voucher": None}), "tool_calls": [], "usage": {}}

    monkeypatch.setattr(agent_mod, "chat_with_tools", fake_chat)
    agent_mod.run_agent(db_session, book_id=book.id, history=[{"role": "user", "content": "你好"}])
    system_prompt = captured[0][0]["content"]
    assert "不得改变、凌驾" in system_prompt
    assert "SaaS 记账工具" in system_prompt
    assert TAGS["software"]["prompt"].splitlines()[0] in system_prompt
