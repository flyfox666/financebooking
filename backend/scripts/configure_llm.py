import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from sqlalchemy import select

from app.core.database import SessionLocal
from app.ledger.llm import gateway
from app.models.llm import LLMProvider

DEFAULTS = {
    "qwen": {
        "name": "qwen-tokenplan",
        "protocol": "openai",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen3.8-flash",
        "vision_model": "qwen3.8-max",
        "is_default": True,
    },
    "ark": {
        "name": "volces-agentplan",
        "protocol": "openai",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-2.1-turbo",
        "vision_model": "glm-5.3",
        "is_default": False,
    },
}


def upsert_provider(db, key_field: str, api_key: str, base_url: str | None, model: str | None) -> None:
    preset = dict(DEFAULTS[key_field])
    preset["api_key"] = api_key
    if base_url:
        preset["base_url"] = base_url
    if model:
        preset["model"] = model

    existing = db.scalar(select(LLMProvider).where(LLMProvider.name == preset["name"]))
    if existing is None:
        db.add(LLMProvider(**preset))
        action = "已创建"
    else:
        existing.api_key = preset["api_key"]
        existing.base_url = preset["base_url"]
        existing.model = preset["model"]
        existing.vision_model = preset["vision_model"]
        existing.enabled = True
        action = "已更新"
    db.commit()
    print(f"{action} 提供商：{preset['name']}（{preset['model']}，Key {gateway.mask_key(api_key)}）")

    if preset["is_default"]:
        gateway.set_default(db, db.scalar(select(LLMProvider).where(LLMProvider.name == preset["name"])).id)
        print(f"已设为默认：{preset['name']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="配置大模型提供商（幂等，可重复执行）")
    parser.add_argument("--qwen-key", default="")
    parser.add_argument("--ark-key", default="")
    parser.add_argument("--qwen-base", default="")
    parser.add_argument("--ark-base", default="")
    parser.add_argument("--qwen-model", default="")
    parser.add_argument("--ark-model", default="")
    args = parser.parse_args()

    with SessionLocal() as db:
        if args.qwen_key:
            upsert_provider(db, "qwen", args.qwen_key, args.qwen_base, args.qwen_model)
        if args.ark_key:
            upsert_provider(db, "ark", args.ark_key, args.ark_base, args.ark_model)

        rows = db.scalars(select(LLMProvider).order_by(LLMProvider.id)).all()
        print("--- 当前提供商列表 ---")
        for row in rows:
            flag = "默认" if row.is_default else "备用"
            print(f"[{flag}] {row.name} · {row.model} · {gateway.mask_key(row.api_key)}")


if __name__ == "__main__":
    main()
