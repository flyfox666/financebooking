import pytest

from app.core.config import get_settings
from app.ledger.exceptions import LLMError
from app.ledger.llm import gateway
from app.models.llm import LLMProvider


@pytest.fixture()
def provider(db_session):
    row = LLMProvider(
        name="test-openai",
        protocol="openai",
        base_url="https://api.example.com/v1",
        api_key="sk-test-1234567890abcdef",
        model="test-model",
        is_default=True,
    )
    db_session.add(row)
    db_session.commit()
    return row


def _fake_openai_response():
    return {"choices": [{"message": {"content": "连通"}}], "usage": {"total_tokens": 9}}


def test_chat_openai_builds_correct_request(db_session, provider, monkeypatch):
    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured.update({"url": url, "headers": headers, "payload": payload})
        return _fake_openai_response()

    monkeypatch.setattr(gateway, "_http_post", fake_post)
    result = gateway.chat(
        db_session,
        messages=[{"role": "user", "content": "你好"}],
        json_mode=True,
        max_tokens=128,
    )

    assert result["content"] == "连通"
    assert captured["url"] == "https://api.example.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-test-1234567890abcdef"
    assert captured["payload"]["model"] == "test-model"
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["payload"]["max_tokens"] == 128


def test_chat_anthropic_protocol_conversion(db_session, provider, monkeypatch):
    provider.protocol = "anthropic"
    provider.base_url = "https://relay.example.com"
    db_session.commit()

    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured.update({"url": url, "headers": headers, "payload": payload})
        return {"content": [{"type": "text", "text": "好的"}], "usage": {}}

    monkeypatch.setattr(gateway, "_http_post", fake_post)
    messages = [
        {"role": "system", "content": "你是会计助手"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "识别这张发票"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ],
        },
    ]
    result = gateway.chat(db_session, messages=messages, json_mode=True)

    assert result["content"] == "好的"
    assert captured["url"] == "https://relay.example.com/v1/messages"
    assert captured["headers"]["x-api-key"] == "sk-test-1234567890abcdef"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert captured["payload"]["system"] == "你是会计助手"
    user_blocks = captured["payload"]["messages"][0]["content"]
    assert user_blocks[0] == {"type": "text", "text": "识别这张发票"}
    assert user_blocks[1]["type"] == "image"
    assert user_blocks[1]["source"]["media_type"] == "image/png"
    assert user_blocks[1]["source"]["data"] == "AAAA"
    assert captured["payload"]["messages"][0]["content"][-1]["type"] == "text"


def test_default_provider_fallback(db_session, provider, monkeypatch):
    provider.is_default = False
    db_session.commit()
    monkeypatch.setattr(gateway, "_http_post", lambda *a, **k: _fake_openai_response())
    result = gateway.chat(db_session, messages=[{"role": "user", "content": "hi"}])
    assert result["content"] == "连通"


def test_no_provider_raises(db_session):
    with pytest.raises(LLMError, match="尚未配置"):
        gateway.chat(db_session, messages=[{"role": "user", "content": "hi"}])


def test_http_error_wrapped(db_session, provider, monkeypatch):
    def fake_post(url, headers, payload, timeout):
        raise LLMError("模型服务返回 401：invalid key")

    monkeypatch.setattr(gateway, "_http_post", fake_post)
    with pytest.raises(LLMError, match="401"):
        gateway.chat(db_session, messages=[{"role": "user", "content": "hi"}])


def test_mask_key():
    assert gateway.mask_key("sk-abcdef1234567890abcd") == "sk-abc****abcd"
    assert gateway.mask_key("short") == "****"


def test_seed_from_env(db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "LLM_API_KEY", "sk-env-seed-key-123456")
    gateway.seed_from_env(db_session)
    seeded = db_session.query(LLMProvider).filter_by(name="env-default").one()
    assert seeded.is_default is True
    assert seeded.model == get_settings().LLM_MODEL
    gateway.seed_from_env(db_session)
    count = db_session.query(LLMProvider).count()
    assert count == 1


def test_provider_crud_and_masking(client, admin_user, auth_headers):
    resp = client.post(
        "/api/llm/providers",
        headers=auth_headers,
        json={
            "name": "qwen-plan",
            "protocol": "openai",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "sk-qwen-secret-9876543210",
            "model": "qwen3.8-max",
            "vision_model": "qwen3.8-max",
            "is_default": True,
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["key_masked"].startswith("sk-qwe")
    assert "secret" not in data["key_masked"]
    assert "sk-qwen-secret-9876543210" not in resp.text

    listing = client.get("/api/llm/providers", headers=auth_headers).json()
    assert len(listing) == 1

    resp = client.patch(
        f"/api/llm/providers/{data['id']}",
        headers=auth_headers,
        json={"model": "qwen3.8-flash"},
    )
    assert resp.json()["model"] == "qwen3.8-flash"

    resp = client.post(f"/api/llm/providers/{data['id']}/default", headers=auth_headers)
    assert resp.json()["is_default"] is True

    resp = client.delete(f"/api/llm/providers/{data['id']}", headers=auth_headers)
    assert resp.status_code == 204


def test_test_provider_endpoint_reports_failure(client, admin_user, auth_headers):
    resp = client.post(
        "/api/llm/providers",
        headers=auth_headers,
        json={
            "name": "bad-relay",
            "protocol": "anthropic",
            "base_url": "https://bad.example.com",
            "api_key": "sk-bad-1234567890",
            "model": "claude-x",
        },
    )
    provider_id = resp.json()["id"]

    def fake_post(url, headers, payload, timeout):
        raise LLMError("模型服务返回 401：unauthorized")

    original = gateway._http_post
    gateway._http_post = fake_post
    try:
        result = client.post(
            f"/api/llm/providers/{provider_id}/test", headers=auth_headers
        ).json()
    finally:
        gateway._http_post = original
    assert result["ok"] is False
    assert "401" in result["error"]


def test_chat_with_tools_normalizes_flat_tool_calls(db_session, provider, monkeypatch):
    """追问第二轮回传：内部扁平 tool_calls 必须转为 OpenAI 完整格式再发送（火山 400 回归）。"""
    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured.update({"payload": payload})
        return {
            "choices": [{"message": {"content": None, "tool_calls": []}}],
            "usage": {},
        }

    monkeypatch.setattr(gateway, "_http_post", fake_post)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "收到设计费5000元"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call-1", "name": "ask_user", "arguments": {"question": "日期是哪天？"}}
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "name": "ask_user", "content": "（已转达）"},
    ]
    gateway.chat_with_tools(db_session, messages=messages, tools=[{"name": "ask_user", "description": "x", "parameters": {}}])

    sent_calls = captured["payload"]["messages"][2]["tool_calls"]
    assert sent_calls[0]["id"] == "call-1"
    assert sent_calls[0]["type"] == "function"
    assert sent_calls[0]["function"]["name"] == "ask_user"
    import json as _json

    args = sent_calls[0]["function"]["arguments"]
    assert isinstance(args, str) and _json.loads(args) == {"question": "日期是哪天？"}
    # tool 消息原样保留
    assert captured["payload"]["messages"][3]["tool_call_id"] == "call-1"


def test_chat_with_tools_keeps_openai_format_calls(db_session, provider, monkeypatch):
    """已是 OpenAI 完整格式的 tool_calls 不被二次转换。"""
    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured.update({"payload": payload})
        return {"choices": [{"message": {"content": None, "tool_calls": []}}], "usage": {}}

    monkeypatch.setattr(gateway, "_http_post", fake_post)
    complete = {
        "id": "call-9",
        "type": "function",
        "function": {"name": "ask_user", "arguments": '{"question":"几点？"}'},
    }
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [complete]},
    ]
    gateway.chat_with_tools(db_session, messages=messages, tools=[])
    assert captured["payload"]["messages"][0]["tool_calls"][0] is complete
