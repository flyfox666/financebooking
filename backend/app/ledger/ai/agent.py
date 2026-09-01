"""对话式记账智能体（第一期）：工具调用循环 + 追问 + 候选凭证输出。

设计要点：
- 工具白名单只含「查询」与「追问」，过账/审核不存在于工具箱（会计责任底线）；
- ask_user 返回终止标记：模型需要用户补充信息时循环立即结束，等待用户回答；
- 最终输出契约：模型 content 为 JSON {"reply": "...", "voucher": {...} 或 null}；
- 每次工具调用都记录进 trace，随响应返回给前端渲染轨迹。
"""

import json
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ledger import aux_service, voucher_service
from app.ledger.exceptions import LedgerError
from app.ledger.llm.gateway import chat_with_tools
from app.models.account import Account
from app.models.contact import Contact
from app.models.tax import Invoice
from app.models.voucher import Voucher

MAX_STEPS = 8
ASK_USER_SENTINEL = "__ASK_USER__"

SYSTEM_PROMPT = """你是「智账」记账助手，服务一家小规模纳税人公司（《小企业会计准则》）。
你的任务：理解用户丢来的单据或描述，必要时先查数据或向用户追问，最后产出候选记账凭证。

规则：
1. 金额一律价税分离：含税价÷(1+征收率)为不含税金额，征收率默认1%；
2. 常用科目：5001主营业务收入 5401主营业务成本 5602管理费用 1001库存现金 1002银行存款 1122应收账款 2202应付账款 2221应交税费 2211应付职工薪酬 4301研发支出 1601固定资产；
3. 科目1122应收账款/2203预收账款要求选「客户」，2202应付账款/1123预付账款要求选「供应商」，1221其他应收款/2241其他应付款要求选「员工」或「其他往来」——如果用户提到了明确的往来对象名字，先用 find_or_create_contact 建立档案，把返回的 contact_id 填进对应分录行；
4. 信息不足以确定科目方向或税率时，调用 ask_user 问用户（一次只问一个问题，用大白话，给选项）；能从单据或常理推断的就自己定，不要问；
5. 查数字必须用工具，不许编造；
6. 最终回答只输出一个 JSON 对象，格式：
   {"reply": "给用户看的中文说明，简洁口语化", "voucher": null}
   或
   {"reply": "说明", "voucher": {"voucher_date": "YYYY-MM-DD", "lines": [{"summary": "摘要", "account_code": "4位科目", "debit": "0.00", "credit": "0.00", "contact_id": 可空}]}}
   借贷合计必须相等。不要输出 JSON 以外的任何文字。"""

TOOLS = [
    {
        "name": "search_invoices",
        "description": "查询发票列表（默认未入账的发票），可按购方或销方名称关键字过滤",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "对方单位名称关键字，可空"},
                "only_unposted": {"type": "boolean", "description": "仅未入账，默认 true"},
            },
        },
    },
    {
        "name": "query_balance",
        "description": "查询某期间科目余额表（period 格式 YYYY-MM），account_code 可空=全部一级科目",
        "parameters": {
            "type": "object",
            "properties": {
                "period": {"type": "string"},
                "account_code": {"type": "string", "description": "4位科目编码，可空"},
            },
            "required": ["period"],
        },
    },
    {
        "name": "query_vouchers",
        "description": "查询凭证列表（period 格式 YYYY-MM），status 可空=全部",
        "parameters": {
            "type": "object",
            "properties": {
                "period": {"type": "string"},
                "status": {"type": "string", "description": "draft/submitted/audited/posted，可空"},
            },
            "required": ["period"],
        },
    },
    {
        "name": "list_contacts",
        "description": "查询往来单位档案，ctype 可空=全部（customer/supplier/employee/other）",
        "parameters": {
            "type": "object",
            "properties": {"ctype": {"type": "string"}},
        },
    },
    {
        "name": "find_or_create_contact",
        "description": "按名称查找往来单位，找不到则创建。返回 contact_id 供凭证分录使用",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "ctype": {"type": "string", "description": "customer/supplier/employee/other"},
            },
            "required": ["name", "ctype"],
        },
    },
    {
        "name": "ask_user",
        "description": "向用户追问一个缺失信息。调用后对话暂停，等待用户回答",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    },
]


def _tool_search_invoices(db: Session, book_id: int, keyword: str = "", only_unposted: bool = True) -> str:
    stmt = select(Invoice).where(Invoice.book_id == book_id)
    if only_unposted:
        stmt = stmt.where(Invoice.voucher_id.is_(None))
    rows = db.scalars(stmt.order_by(Invoice.id.desc()).limit(50)).all()
    if keyword:
        kw = keyword.strip()
        rows = [r for r in rows if kw in (r.seller_name or "") or kw in (r.buyer_name or "")]
    return json.dumps(
        [
            {
                "invoice_id": r.id,
                "kind": "销项" if r.kind == "sales" else "进项",
                "date": str(r.invoice_date),
                "counterparty": r.seller_name if r.kind == "purchase" else r.buyer_name,
                "goods": r.goods_name,
                "amount_total": str(r.amount_total),
                "tax_rate": str(r.tax_rate),
                "status": r.status,
            }
            for r in rows
        ],
        ensure_ascii=False,
    )


def _tool_query_balance(db: Session, book_id: int, period: str, account_code: str = "") -> str:
    from app.ledger.balances import trial_balance

    report = trial_balance(db, book_id=book_id, period=period, complete=True)
    rows = report.get("rows", [])
    if account_code:
        rows = [r for r in rows if r["account_code"].startswith(account_code)]
    keys = (
        "account_code", "account_name", "opening_debit", "opening_credit",
        "period_debit", "period_credit", "closing_debit", "closing_credit",
    )
    return json.dumps(
        [{k: r[k] for k in keys} for r in rows if r.get("level") == 1],
        ensure_ascii=False,
    )


def _tool_query_vouchers(db: Session, book_id: int, period: str, status: str = "") -> str:
    stmt = select(Voucher).where(Voucher.book_id == book_id, Voucher.period == period)
    if status:
        stmt = stmt.where(Voucher.status == status)
    rows = db.scalars(stmt.order_by(Voucher.voucher_no)).all()
    return json.dumps(
        [
            {
                "voucher_id": v.id,
                "no": f"记字第{v.voucher_no:04d}号",
                "date": str(v.voucher_date),
                "status": v.status,
                "total": str(v.total_debit),
                "first_summary": v.lines[0].summary if v.lines else "",
            }
            for v in rows
        ],
        ensure_ascii=False,
    )


def _tool_list_contacts(db: Session, book_id: int, ctype: str = "") -> str:
    rows = aux_service.list_contacts(db, book_id, ctype or None)
    return json.dumps(
        [{"contact_id": c.id, "name": c.name, "ctype": c.ctype, "active": c.is_active} for c in rows],
        ensure_ascii=False,
    )


def _tool_find_or_create_contact(db: Session, book_id: int, name: str, ctype: str) -> str:
    existing = db.scalar(select(Contact).where(Contact.book_id == book_id, Contact.name == name.strip()))
    if existing:
        return json.dumps({"contact_id": existing.id, "name": existing.name, "ctype": existing.ctype, "created": False}, ensure_ascii=False)
    try:
        contact = aux_service.create_contact(db, book_id=book_id, name=name, ctype=ctype)
    except LedgerError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    return json.dumps({"contact_id": contact.id, "name": contact.name, "ctype": contact.ctype, "created": True}, ensure_ascii=False)


def execute_tool(db: Session, book_id: int, name: str, arguments: dict) -> str:
    try:
        if name == "search_invoices":
            return _tool_search_invoices(
                db, book_id,
                keyword=str(arguments.get("keyword", "")),
                only_unposted=bool(arguments.get("only_unposted", True)),
            )
        if name == "query_balance":
            return _tool_query_balance(
                db, book_id,
                period=str(arguments.get("period", "")),
                account_code=str(arguments.get("account_code", "")),
            )
        if name == "query_vouchers":
            return _tool_query_vouchers(
                db, book_id,
                period=str(arguments.get("period", "")),
                status=str(arguments.get("status", "")),
            )
        if name == "list_contacts":
            return _tool_list_contacts(db, book_id, ctype=str(arguments.get("ctype", "")))
        if name == "find_or_create_contact":
            return _tool_find_or_create_contact(
                db, book_id,
                name=str(arguments.get("name", "")),
                ctype=str(arguments.get("ctype", "customer")),
            )
        if name == "ask_user":
            return ASK_USER_SENTINEL
    except Exception as exc:
        return json.dumps({"error": f"工具执行失败: {exc}"}, ensure_ascii=False)
    return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)


def extract_json(text: str) -> dict | None:
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def run_agent(db: Session, *, book_id: int, history: list[dict], doc_context: str = "") -> dict:
    """执行智能体循环，返回 {reply, voucher, trace, stopped_by_ask_user}。"""
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if doc_context:
        messages[0]["content"] += f"\n\n当前用户提供的单据解析结果（已结构化，可直接使用）：\n{doc_context}"
    messages.extend(history)

    trace: list[dict] = []
    stopped_by_ask_user = False
    ask_question = ""
    final_content = ""

    for _step in range(MAX_STEPS):
        result = chat_with_tools(db, messages=messages, tools=TOOLS)
        tool_calls = result.get("tool_calls") or []
        if not tool_calls:
            final_content = result.get("content") or ""
            break

        messages.append({
            "role": "assistant",
            "content": result.get("content") or "",
            "tool_calls": [
                {"id": call["id"], "name": call["name"], "arguments": call.get("arguments", {})}
                for call in tool_calls
            ],
        })
        for call in tool_calls:
            name, arguments = call["name"], call.get("arguments", {})
            output = execute_tool(db, book_id, name, arguments)
            trace.append({"tool": name, "arguments": arguments, "output": output[:500]})
            if output == ASK_USER_SENTINEL:
                stopped_by_ask_user = True
                ask_question = str(arguments.get("question", "请补充一些信息"))
                messages.append({"role": "tool", "tool_call_id": call["id"], "name": name, "content": "（问题已转达给用户，等待回答）"})
                break
            messages.append({"role": "tool", "tool_call_id": call["id"], "name": name, "content": output})
        if stopped_by_ask_user:
            break

    if stopped_by_ask_user:
        return {"reply": ask_question, "voucher": None, "trace": trace, "stopped_by_ask_user": True}

    payload = extract_json(final_content)
    if payload and isinstance(payload.get("voucher"), dict):
        return {
            "reply": str(payload.get("reply", "")),
            "voucher": payload["voucher"],
            "trace": trace,
            "stopped_by_ask_user": False,
        }
    return {
        "reply": final_content.strip() or "（模型未返回有效内容，请重试）",
        "voucher": None,
        "trace": trace,
        "stopped_by_ask_user": False,
    }
