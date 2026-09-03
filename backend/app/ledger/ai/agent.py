"""对话式记账智能体（第一期）：工具调用循环 + 追问 + 候选凭证输出。

设计要点：
- 工具白名单只含「查询」与「追问」，过账/审核不存在于工具箱（会计责任底线）；
- ask_user 返回终止标记：模型需要用户补充信息时循环立即结束，等待用户回答；
- 最终输出契约：模型 content 为 JSON {"reply": "...", "voucher": {...} 或 null}；
- 每次工具调用都记录进 trace，随响应返回给前端渲染轨迹。
"""

import json
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ledger import aux_service, voucher_service
from app.ledger.exceptions import LedgerError
from app.ledger.llm.gateway import chat_with_tools
from app.models.account import Account
from app.models.book import Book
from app.models.contact import Contact
from app.models.tax import Invoice
from app.models.voucher import Voucher

MAX_STEPS = 8
ASK_USER_SENTINEL = "__ASK_USER__"

SYSTEM_PROMPT = """你是「智账」记账助手，服务一家公司（《小企业会计准则》），纳税人性质见下方账套信息。
你的任务：理解用户丢来的单据或描述，必要时先查数据或向用户追问，最后产出候选记账凭证。

规则：
1. 价税处理规则按账套信息中的「纳税人性质」执行（见下方注入的价税规则）；
2. 常用科目：5001主营业务收入 5401主营业务成本 5602管理费用 1001库存现金 1002银行存款 1122应收账款 2202应付账款 2221应交税费 2211应付职工薪酬 4301研发支出 1601固定资产；
3. 涉及往来科目（1122/2203需客户、2202/1123需供应商、1221/2241需员工或其他往来）时，严格按顺序处理：
   ① 先用 search_contacts 按名称关键字查现有往来档案；
   ② 查到唯一明确匹配 → 直接使用返回的 contact_id，并在回复中说明挂接的往来单位名称；
   ③ 没查到、或多个候选不确定 → 用 ask_user 问用户（说明没找到，建议按业务判断的类型新建，请用户确认或纠正名称）；
   ④ 用户明确同意后才调用 find_or_create_contact 新建；
   ⑤ 严禁在用户未确认的情况下静默新建往来档案；
4. 信息不足以确定科目方向或税率时，调用 ask_user 问用户（一次只问一个问题，用大白话，给选项）；能从单据或常理推断的就自己定，不要问；
5. 查数字必须用工具，不许编造；
6. **发票购方校验**：如果单据是发票，必须检查购方名称是否与当前账套的公司名称匹配（模糊匹配即可，如包含关系）。如果不匹配，必须用 ask_user 警告用户"这张发票的购方是XXX，但当前账套是YYY，请确认是否要入账"，等用户确认后再处理。
7. **重复入账检测**：每次生成凭证前必须调用 check_duplicate（有发票号就传 invoice_no，同时传 amount、date、keyword）。如果返回 has_duplicate=true，必须用 ask_user 提示用户"疑似重复入账：已存在 记字第X号（日期/摘要/金额），请确认是否仍要入账"，等用户明确确认后才继续；用户说是重复或不需要时，voucher 置 null。
8. **现金流量标注**：凡现金类科目（1001库存现金/1002银行存款/1012其他货币资金）的分录行，必须按该笔现金收支的经济实质（读摘要+对方科目判断）填 cf_item 字段：
   收钱：sales=销售商品提供劳务收到的现金，invest_return=取得投资收益，asset_dispose=处置资产收回，capital_in=吸收投资收到的现金，borrow_in=取得借款收到的现金，other_in=收到其他与经营活动有关的现金
   付钱：purchase=购买商品接受劳务支付，staff=支付职工薪酬，taxes=支付各项税费，capex=购建固定资产等长期资产支付，invest_out=投资支付，borrow_repay=偿还债务支付，dividend=分红付息支付，other_out=支付其他与经营活动有关的现金
   例："支付张三差旅报销"→other_out；"购买办公电脑"→capex；"收到客户货款"→sales；"交上月增值税"→taxes。非现金行不要带 cf_item。
9. **支付截图**（解析结果的 doc_type=payment）：字段 channel（微信/支付宝/银行）、pay_direction（pay=我们付出，receive=我们收到）、counterparty（对方）、amount_total（金额）、pay_time（支付时间）、note（事项备注）。记账要点：
   - 付出：借记费用/资产/往来科目，贷记资金科目——微信/支付宝零钱余额走 1012 其他货币资金，绑定的银行卡走 1002 银行存款；分不清从哪个渠道付的，用 ask_user 问；
   - 收到：借记资金科目，贷记 5001 主营业务收入（按价税规则分离）或往来科目；
   - note 看不出资金用途/性质时用 ask_user 问（如「这笔付给XX的 500 元是买什么/什么用途？」），能从 note 常理推断的不要问；
   - voucher_date 取 pay_time 的日期部分；摘要写「对方+事项」方便日后翻账。
10. 最终回答只输出一个 JSON 对象，格式：
   {"reply": "给用户看的中文说明，简洁口语化", "voucher": null}
   或
   {"reply": "说明", "voucher": {"voucher_date": "YYYY-MM-DD", "lines": [{"summary": "摘要", "account_code": "4位科目", "debit": "0.00", "credit": "0.00", "contact_id": 可空, "cf_item": "仅现金类科目行必填"}]}}
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
        "name": "search_contacts",
        "description": "按名称关键字模糊查询往来单位档案。涉及往来科目前必须先调用本工具查现有档案，避免重复建档",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "名称关键字，如公司简称"},
                "ctype": {"type": "string", "description": "customer/supplier/employee/other，可空=全部"},
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "find_or_create_contact",
        "description": "新建往来单位（仅在用户明确同意新建后调用；同名档案已存在时直接复用返回 contact_id）",
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
        "name": "check_duplicate",
        "description": "重复入账检测：生成凭证前必须调用。按发票号查已导入的发票，按金额（±30天日期窗口、可选摘要关键字）查疑似重复凭证",
        "parameters": {
            "type": "object",
            "properties": {
                "invoice_no": {"type": "string", "description": "发票号，可空"},
                "amount": {"type": "string", "description": "凭证合计金额（借贷总额），可空"},
                "date": {"type": "string", "description": "业务日期 YYYY-MM-DD，可空"},
                "keyword": {"type": "string", "description": "摘要关键字，可空"},
            },
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


def _tool_search_contacts(db: Session, book_id: int, keyword: str, ctype: str = "") -> str:
    """模糊查询往来档案：双向包含匹配（档案名含关键字，或关键字含档案名）。"""
    kw = keyword.strip()
    rows = aux_service.list_contacts(db, book_id, ctype or None)
    matches = [
        c for c in rows
        if kw and (kw in (c.name or "") or (c.name or "") in kw)
    ]
    return json.dumps(
        [
            {"contact_id": c.id, "name": c.name, "ctype": c.ctype, "active": c.is_active}
            for c in matches
        ],
        ensure_ascii=False,
    )


def _tool_find_or_create_contact(db: Session, book_id: int, name: str, ctype: str) -> str:
    existing = db.scalar(select(Contact).where(Contact.book_id == book_id, Contact.name == name.strip()))
    if existing:
        result = {"contact_id": existing.id, "name": existing.name, "ctype": existing.ctype, "created": False}
        if existing.ctype != ctype:
            result["ctype_mismatch"] = True
            result["note"] = f"已存在同名档案但类型是 {existing.ctype}，与请求的 {ctype} 不一致，请与用户确认后再使用"
        return json.dumps(result, ensure_ascii=False)
    try:
        contact = aux_service.create_contact(db, book_id=book_id, name=name, ctype=ctype)
    except LedgerError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    return json.dumps({"contact_id": contact.id, "name": contact.name, "ctype": contact.ctype, "created": True}, ensure_ascii=False)


def _tool_check_duplicate(db: Session, book_id: int, invoice_no: str = "", amount: str = "", date_str: str = "", keyword: str = "") -> str:
    """重复入账检测：发票号精确匹配**已入账**（已关联凭证）的发票；金额匹配 ±30 天窗口内的现有凭证。

    台账中已导入但尚未生成凭证的发票不算重复——那正是"发票驱动记账"的正常起点。
    """
    result: dict = {"invoice_no_match": [], "similar_vouchers": [], "has_duplicate": False}

    no = invoice_no.strip()
    if no:
        inv_rows = db.scalars(select(Invoice).where(Invoice.book_id == book_id, Invoice.invoice_no == no)).all()
        result["invoice_no_match"] = [
            {
                "invoice_id": r.id,
                "kind": "销项" if r.kind == "sales" else "进项",
                "date": str(r.invoice_date),
                "goods": r.goods_name,
                "amount_total": str(r.amount_total),
                "posted": bool(r.voucher_id),
            }
            for r in inv_rows
            if r.voucher_id
        ]

    amt: Decimal | None = None
    try:
        amt = Decimal(amount.strip()) if amount.strip() else None
    except Exception:
        amt = None
    biz_date = None
    try:
        biz_date = date.fromisoformat(date_str.strip()) if date_str.strip() else None
    except ValueError:
        biz_date = None

    if amt is not None:
        # 金额是主信号：金额相等 + 日期 ±30 天窗口即视为疑似重复（不做摘要硬过滤，避免措辞差异漏检）
        stmt = select(Voucher).where(Voucher.book_id == book_id, Voucher.total_debit == amt)
        rows = db.scalars(stmt.order_by(Voucher.voucher_date.desc()).limit(30)).all()
        if biz_date:
            rows = [v for v in rows if abs((v.voucher_date - biz_date).days) <= 30]
    elif keyword.strip():
        # 无金额时按摘要关键字兜底（近 90 天）
        kw = keyword.strip()
        stmt = select(Voucher).where(Voucher.book_id == book_id).order_by(Voucher.voucher_date.desc()).limit(200)
        rows = [v for v in db.scalars(stmt).all() if kw in (v.lines[0].summary if v.lines else "")]
        if biz_date:
            rows = [v for v in rows if abs((v.voucher_date - biz_date).days) <= 90]
    else:
        rows = []
    result["similar_vouchers"] = [
        {
            "voucher_id": v.id,
            "no": f"记字第{v.voucher_no:04d}号",
            "date": str(v.voucher_date),
            "status": v.status,
            "total": str(v.total_debit),
            "first_summary": v.lines[0].summary if v.lines else "",
        }
        for v in rows
    ]

    result["has_duplicate"] = bool(result["invoice_no_match"] or result["similar_vouchers"])
    return json.dumps(result, ensure_ascii=False)


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
        if name == "search_contacts":
            return _tool_search_contacts(
                db, book_id,
                keyword=str(arguments.get("keyword", "")),
                ctype=str(arguments.get("ctype", "")),
            )
        if name == "find_or_create_contact":
            return _tool_find_or_create_contact(
                db, book_id,
                name=str(arguments.get("name", "")),
                ctype=str(arguments.get("ctype", "customer")),
            )
        if name == "check_duplicate":
            return _tool_check_duplicate(
                db, book_id,
                invoice_no=str(arguments.get("invoice_no", "")),
                amount=str(arguments.get("amount", "")),
                date_str=str(arguments.get("date", "")),
                keyword=str(arguments.get("keyword", "")),
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


DUP_WARN_MARK = "疑似重复入账"


def _dup_already_confirmed(history: list[dict]) -> bool:
    """对话中已出现过重复警告且用户已回应（才会进入下一轮）→ 视为用户已确认，放行。"""
    return any(m.get("role") == "assistant" and DUP_WARN_MARK in str(m.get("content", "")) for m in history)


def _sanitize_contacts(db: Session, book_id: int, voucher: dict) -> int:
    """硬兜底：模型可能编造 contact_id（未调 find_or_create_contact 就直接写 ID）。
    返回前逐行校验，无效的往来 ID 置空（凭证卡片里可手动补选），返回清理行数。"""
    from app.models.contact import Contact

    cleaned = 0
    for line in voucher.get("lines") or []:
        cid = line.get("contact_id")
        if not cid:
            continue
        contact = db.get(Contact, int(cid))
        if contact is None or not contact.is_active or contact.book_id != book_id:
            line["contact_id"] = None
            cleaned += 1
    return cleaned


def _hard_check_duplicate(db: Session, book_id: int, voucher: dict) -> str | None:
    """代码层硬兜底：模型生成的凭证在返回前强制查重，命中则返回警告文本（未命中返回 None）。

    不依赖模型自觉调用 check_duplicate 工具——提示词是软约束，这里是硬约束。
    """
    lines = voucher.get("lines") or []
    if not lines:
        return None
    try:
        amt = sum(Decimal(str(l.get("debit") or "0")) for l in lines)
        if amt <= 0:
            amt = sum(Decimal(str(l.get("credit") or "0")) for l in lines)
    except Exception:
        return None
    if amt <= 0:
        return None
    d = str(voucher.get("voucher_date") or date.today().isoformat())
    kw = str(lines[0].get("summary") or "")
    try:
        data = json.loads(_tool_check_duplicate(db, book_id, amount=str(amt), date_str=d, keyword=kw))
    except Exception:
        return None
    if not data.get("has_duplicate"):
        return None
    parts = [
        f"{v['no']}（{v['date']} · {v['first_summary']} · {v['total']} 元 · {v['status']}）"
        for v in data.get("similar_vouchers", [])
    ]
    parts += [
        f"发票号已登记：{r['date']} · {r['goods']} · {r['amount_total']} 元"
        for r in data.get("invoice_no_match", [])
    ]
    return (
        f"⚠️ {DUP_WARN_MARK}：已存在相同金额的凭证/发票记录——{'；'.join(parts)}。"
        "请确认是否仍要入账：回复「确认入账」我就继续生成，或告诉我这笔和上面那笔的区别。"
    )


def _tax_rule(book: Book | None) -> str:
    """按账套纳税人性质生成价税规则，注入 system prompt（拆不拆税由此决定，不写死）。"""
    if book and book.taxpayer_type == "general":
        return (
            "\n- 纳税人性质：一般纳税人\n- 价税规则（两个方向都要价税分离）：\n"
            "  ① 销项（我们开出的发票/收入）：税额=不含税价×税率，贷记 2221 应交税费-应交增值税（销项）；\n"
            "  ② 进项（我们收到的发票）：税额=不含税价×税率，借记 2221 应交税费-应交增值税（进项），"
            "税率以发票票面为准（13%/9%/6%等），票面没有时问用户；\n"
            "  ③ 不含税金额与税额以发票票面列示为准，票面只有价税合计时按票面税率反算。"
        )
    # small_scale 或未设置（默认小规模，安全）
    return (
        "\n- 纳税人性质：小规模纳税人\n- 价税规则（进项税不可抵扣，方向不同处理完全不同）：\n"
        "  ① 销项（我们开出去的发票/收入）：价税分离，不含税价=含税价÷(1+征收率)，征收率默认1%，"
        "税额贷记 2221 应交税费-应交增值税；\n"
        "  ② 进项（我们收到的发票/成本费用/购资产）：**严禁拆税**，价税合计全额计入费用/成本/固定资产等科目，"
        "绝不动用 2221，也不做「进项税额」行——拆了也不可抵扣，只会做错账。"
    )


def run_agent(db: Session, *, book_id: int, history: list[dict], doc_context: str = "") -> dict:
    """执行智能体循环，返回 {reply, voucher, trace, stopped_by_ask_user}。"""
    # 查询当前账套的公司信息，并注入当前日期（模型无法自行得知今天几号）
    book = db.get(Book, book_id)
    today = date.today()
    company_info = f"\n\n今天是 {today.isoformat()}（{today.strftime('%Y年%m月%d日')}）。用户没有说明业务日期时，一律以今天作为凭证日期和查重日期，严禁编造其他日期。"
    if book:
        company_info += (
            f"\n\n当前账套信息：\n- 公司名称：{book.name or '（未设置）'}\n- 税号：{book.tax_no or '（未设置）'}"
            + _tax_rule(book)
        )
    else:
        company_info += "\n\n当前账套信息：未获取到账套，默认按小规模纳税人处理价税。" + _tax_rule(None)

    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT + company_info}]
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
        # 硬兜底：凭证出门前代码层强制查重（用户本轮已确认过重复的除外）
        if not _dup_already_confirmed(history):
            warning = _hard_check_duplicate(db, book_id, payload["voucher"])
            if warning:
                trace.append({"tool": "check_duplicate(hard)", "arguments": {}, "output": warning[:500]})
                return {"reply": warning, "voucher": None, "trace": trace, "stopped_by_ask_user": True}
        cleaned = _sanitize_contacts(db, book_id, payload["voucher"])
        if cleaned:
            trace.append({"tool": "sanitize_contacts(hard)", "arguments": {}, "output": f"清理了 {cleaned} 行无效的往来单位 ID，请在凭证卡片中补选"})
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
