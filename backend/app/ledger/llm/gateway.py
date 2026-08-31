import base64
import mimetypes
from pathlib import Path

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.ledger.exceptions import LLMError
from app.models.llm import LLMProvider

ANTHROPIC_VERSION = "2023-06-01"


def mask_key(key: str) -> str:
    if len(key) > 12:
        return f"{key[:6]}****{key[-4:]}"
    return "****"


def _resolve_provider(db: Session, provider_id: int | None = None) -> LLMProvider:
    if provider_id is not None:
        provider = db.get(LLMProvider, provider_id)
        if provider is None:
            raise LLMError("模型服务不存在")
        if not provider.enabled:
            raise LLMError(f"模型服务 {provider.name} 已停用")
        return provider
    provider = db.scalar(
        select(LLMProvider).where(LLMProvider.is_default.is_(True), LLMProvider.enabled.is_(True))
    )
    if provider is None:
        provider = db.scalar(select(LLMProvider).where(LLMProvider.enabled.is_(True)).order_by(LLMProvider.id))
    if provider is None:
        raise LLMError("尚未配置任何可用的大模型服务")
    return provider


def _http_post(url: str, headers: dict, payload: dict, timeout: float) -> dict:
    response = httpx.post(url, headers=headers, json=payload, timeout=timeout)
    if response.status_code >= 400:
        raise LLMError(f"模型服务返回 {response.status_code}：{response.text[:300]}")
    return response.json()


def _openai_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/chat/completions"


def _anthropic_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/messages"
    return f"{base}/v1/messages"


def _to_anthropic_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    system_parts: list[str] = []
    converted: list[dict] = []
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")
        if role == "system":
            if isinstance(content, str):
                system_parts.append(content)
            else:
                for part in content:
                    if part.get("type") == "text":
                        system_parts.append(part["text"])
            continue
        if isinstance(content, str):
            blocks = [{"type": "text", "text": content}]
        else:
            blocks = []
            for part in content:
                if part.get("type") == "text":
                    blocks.append({"type": "text", "text": part["text"]})
                elif part.get("type") == "image_url":
                    data_url = part["image_url"]["url"]
                    header, b64 = data_url.split(",", 1)
                    media_type = header[len("data:") : header.find(";")]
                    blocks.append(
                        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}}
                    )
        converted.append({"role": role, "content": blocks})
    return "\n".join(system_parts), converted


def _chat_openai(provider: LLMProvider, messages: list[dict], json_mode: bool, max_tokens: int, timeout: float) -> dict:
    payload: dict = {"model": provider.model, "messages": messages, "max_tokens": max_tokens}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    data = _http_post(
        _openai_url(provider.base_url),
        {"Authorization": f"Bearer {provider.api_key}"},
        payload,
        timeout,
    )
    content = data["choices"][0]["message"]["content"]
    return {"content": content, "usage": data.get("usage", {})}


def _chat_anthropic(provider: LLMProvider, messages: list[dict], json_mode: bool, max_tokens: int, timeout: float) -> dict:
    system, converted = _to_anthropic_messages(messages)
    if json_mode and converted:
        last = converted[-1]["content"]
        last.append({"type": "text", "text": "只输出一个合法的 JSON 对象，不要有任何其他文字。"})
    payload: dict = {"model": provider.model, "max_tokens": max_tokens, "messages": converted}
    if system:
        payload["system"] = system
    data = _http_post(
        _anthropic_url(provider.base_url),
        {"x-api-key": provider.api_key, "anthropic-version": ANTHROPIC_VERSION},
        payload,
        timeout,
    )
    text = "".join(block.get("text", "") for block in data.get("content", []))
    return {"content": text, "usage": data.get("usage", {})}


def chat(
    db: Session,
    *,
    messages: list[dict],
    provider_id: int | None = None,
    json_mode: bool = False,
    max_tokens: int = 2048,
) -> dict:
    provider = _resolve_provider(db, provider_id)
    timeout = float(get_settings().LLM_TIMEOUT_SECONDS)
    if provider.protocol == "anthropic":
        return _chat_anthropic(provider, messages, json_mode, max_tokens, timeout)
    return _chat_openai(provider, messages, json_mode, max_tokens, timeout)


def test_provider(db: Session, provider_id: int) -> dict:
    provider = db.get(LLMProvider, provider_id)
    if provider is None:
        raise LLMError("模型服务不存在")
    try:
        result = chat(
            db,
            provider_id=provider_id,
            messages=[{"role": "user", "content": "请只回复两个字：连通"}],
            max_tokens=16,
        )
    except LLMError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "model": provider.model, "reply": result["content"][:100]}


def set_default(db: Session, provider_id: int) -> None:
    for row in db.scalars(select(LLMProvider).where(LLMProvider.is_default.is_(True))):
        row.is_default = False
    provider = db.get(LLMProvider, provider_id)
    if provider is None:
        raise LLMError("模型服务不存在")
    provider.is_default = True
    provider.enabled = True
    db.commit()


def seed_from_env(db: Session) -> None:
    settings = get_settings()
    if not settings.LLM_API_KEY:
        return
    count = db.scalar(select(func.count()).select_from(LLMProvider))
    if count:
        return
    db.add(
        LLMProvider(
            name="env-default",
            protocol="openai",
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY,
            model=settings.LLM_MODEL,
            is_default=True,
        )
    )
    db.commit()


def image_message(text: str, image_path: Path) -> dict:
    media_type = mimetypes.guess_type(str(image_path))[0] or "image/png"
    b64 = base64.b64encode(image_path.read_bytes()).decode()
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}},
        ],
    }
