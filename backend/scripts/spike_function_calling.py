"""Function Calling Spike：验证当前默认提供商是否支持 OpenAI tools 协议。

验证三步：① 网关接受 tools 参数 ② 模型产生 tool_calls ③ 回传工具结果后给出最终回答。
在容器内运行：docker compose exec backend python scripts/spike_function_calling.py
"""

import json
import sys

import httpx
from sqlalchemy import select

sys.path.insert(0, ".")

from app.core.database import SessionLocal
from app.models.llm import LLMProvider

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "query_trial_balance",
            "description": "查询指定期间的科目余额表，返回各科目本期发生额与期末余额",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {"type": "string", "description": "会计期间，格式 YYYY-MM"},
                },
                "required": ["period"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_vouchers_unposted",
            "description": "查询指定期间尚未过账的凭证列表",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {"type": "string", "description": "会计期间，格式 YYYY-MM"},
                },
                "required": ["period"],
            },
        },
    },
]


def main() -> None:
    with SessionLocal() as db:
        provider = db.scalar(
            select(LLMProvider).where(LLMProvider.is_default.is_(True))
        )
    print(f"提供商: {provider.name} · {provider.model} · {provider.base_url}")

    url = provider.base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {provider.api_key}"}

    messages = [
        {"role": "system", "content": "你是记账助手，回答数据问题前必须先调用工具查询，不许编造数字。"},
        {"role": "user", "content": "帮我看看 2026-08 的科目余额表里银行存款还有多少钱？"},
    ]

    try:
        r1 = httpx.post(
            url, headers=headers, timeout=90,
            json={"model": provider.model, "messages": messages, "tools": TOOLS, "max_tokens": 512},
        )
    except Exception as exc:
        print(f"✗ 请求异常: {exc}")
        sys.exit(1)

    print(f"① HTTP {r1.status_code}")
    if r1.status_code >= 400:
        print(f"✗ 网关拒绝 tools 参数: {r1.text[:300]}")
        sys.exit(1)

    data = r1.json()
    choice = data["choices"][0]
    message = choice["message"]
    print(f"① finish_reason: {choice.get('finish_reason')}")

    tool_calls = message.get("tool_calls") or []
    if not tool_calls:
        print(f"△ 模型未发起工具调用，直接文本回答: {str(message.get('content'))[:200]}")
        print("△ 结论: 网关接受 tools 但该模型未调用（可尝试其他模型）")
        sys.exit(0)

    call = tool_calls[0]
    print(f"② 模型发起调用: {call['function']['name']} 参数: {call['function']['arguments']}")

    messages.append(message)
    fake_result = json.dumps(
        {"1002 银行存款": {"期初": 0, "本期借方": 561300.00, "本期贷方": 96930.00, "期末借方": 464370.00}},
        ensure_ascii=False,
    )
    messages.append({
        "role": "tool",
        "tool_call_id": call["id"],
        "content": fake_result,
    })

    r2 = httpx.post(
        url, headers=headers, timeout=90,
        json={"model": provider.model, "messages": messages, "tools": TOOLS, "max_tokens": 512},
    )
    print(f"③ 二次响应 HTTP {r2.status_code}")
    if r2.status_code >= 400:
        print(f"✗ 工具结果回传失败: {r2.text[:300]}")
        sys.exit(1)

    final = r2.json()["choices"][0]["message"]["content"]
    print(f"③ 最终回答: {str(final)[:300]}")
    if "464370" in str(final) or "46.43" in str(final):
        print("✓ PASS：function calling 三步全链路可用，且模型使用了工具返回的真实数据")
    else:
        print("△ 模型回答未引用工具数据，请人工检查上述回答")


if __name__ == "__main__":
    main()
