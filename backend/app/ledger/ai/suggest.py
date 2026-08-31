import json
import mimetypes
import re
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ledger.attachment_service import save_attachment
from app.ledger.exceptions import LLMError, VoucherError
from app.ledger.llm import gateway
from app.ledger.voucher_service import create_voucher
from app.models.account import Account
from app.models.ai import AIDoc
from app.models.voucher import Voucher

TWO = Decimal("0.01")

SYSTEM_PROMPT = (
    "你是严格遵守《小企业会计准则》的记账引擎，服务对象是上海一家小规模纳税人、"
    "小型微利的 AI 应用公司。记账规则：增值税征收率1%，收入按价税分离入账，"
    "费用按价税合计入账（进项不可抵扣），红冲使用负数行而非反向科目，"
    "季末销售额未达30万起征点时免征增值税转入营业外收入。"
    "只能使用科目字典中给出的启用末级科目，借贷必须平衡，金额为两位小数字符串。"
)

FEW_SHOT = (
    "示例1：收到技术服务费11300元（价税合计，1%征收率）→\n"
    '{"voucher_date":"2026-08-06","lines":[{"summary":"收到技术服务费","account_code":"1122","debit":"11300.00","credit":"0.00"},'
    '{"summary":"确认技术服务收入","account_code":"5001","debit":"0.00","credit":"11188.12"},'
    '{"summary":"计提增值税","account_code":"2221","debit":"0.00","credit":"111.88"}]}\n'
    "示例2：支付云服务器费3200元（取得普票，价税合计入费用）→\n"
    '{"voucher_date":"2026-08-05","lines":[{"summary":"支付云服务器费","account_code":"5401","debit":"3200.00","credit":"0.00"},'
    '{"summary":"银行付款","account_code":"1002","debit":"0.00","credit":"3200.00"}]}'
)


def _dec(value) -> Decimal | None:
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, AttributeError, ValueError):
        return None


def _parse_model_json(content: str) -> dict:
    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    return json.loads(text)


def account_whitelist(db: Session, book_id: int) -> list[dict]:
    accounts = db.scalars(
        select(Account)
        .where(Account.book_id == book_id, Account.is_leaf.is_(True), Account.is_active.is_(True))
        .order_by(Account.code)
    ).all()
    return [{"code": a.code, "name": a.name} for a in accounts]


def build_messages(doc_type: str, fields: dict, note: str, whitelist: list[dict]) -> list[dict]:
    payload = {"doc_type": doc_type, "invoice_fields": fields} if doc_type == "invoice" else {"note": note}
    user = (
        f"科目字典（只能从中选择）：{json.dumps(whitelist, ensure_ascii=False)}\n\n"
        f"业务参考示例：\n{FEW_SHOT}\n\n"
        f"本次业务：{json.dumps(payload, ensure_ascii=False)}\n\n"
        '请输出凭证 JSON：{"voucher_date":"YYYY-MM-DD","lines":[{"summary":"","account_code":"","debit":"0.00","credit":"0.00"}]}，'
        "只输出 JSON，不要其他文字。"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def validate_candidate(db: Session, book_id: int, payload: dict, invoice_fields: dict | None) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    lines = payload.get("lines") or []
    if len(lines) < 2:
        errors.append("分录不足两行")
        return errors, warnings

    total_debit = Decimal("0")
    total_credit = Decimal("0")
    for index, line in enumerate(lines, start=1):
        code = str(line.get("account_code") or "").strip()
        account = db.scalar(
            select(Account).where(Account.book_id == book_id, Account.code == code)
        )
        if account is None:
            errors.append(f"第 {index} 行科目 {code} 不存在")
            continue
        if not account.is_active or not account.is_leaf:
            errors.append(f"第 {index} 行科目 {code} 不是启用中的末级科目")
        debit = _dec(line.get("debit")) or Decimal("0")
        credit = _dec(line.get("credit")) or Decimal("0")
        if debit > 0 and credit > 0:
            errors.append(f"第 {index} 行借贷同时有值")
        if debit == 0 and credit == 0:
            errors.append(f"第 {index} 行借贷同时为零")
        total_debit += debit
        total_credit += credit

    if total_debit != total_credit:
        errors.append(f"借贷不平衡：借方合计 {total_debit}，贷方合计 {total_credit}")
    elif total_debit == 0 and not errors:
        errors.append("合计金额为零")

    if invoice_fields and not errors:
        expected = _dec(invoice_fields.get("amount_total"))
        if expected and abs(total_debit - abs(expected)) > TWO:
            warnings.append(
                f"候选凭证合计 {total_debit} 与票据金额 {abs(expected)} 不一致，请人工核对"
            )
        if invoice_fields.get("status") == "red":
            serialized = json.dumps(payload, ensure_ascii=False)
            if not re.search(r'"-\d+\.\d{2}"', serialized):
                warnings.append("票据为红冲发票，请确认使用负数行冲减")
    return errors, warnings


def suggest_voucher(
    db: Session,
    *,
    book_id: int,
    doc_type: str,
    fields: dict | None = None,
    note: str = "",
) -> dict:
    whitelist = account_whitelist(db, book_id)
    if not whitelist:
        raise VoucherError("账套科目未初始化，无法生成候选凭证")
    messages = build_messages(doc_type, fields or {}, note, whitelist)
    result = gateway.chat(db, messages=messages, json_mode=True)
    try:
        payload = _parse_model_json(result["content"])
    except json.JSONDecodeError:
        raise LLMError("模型输出不是合法 JSON：" + result["content"][:200])

    invoice_fields = fields if doc_type == "invoice" else None
    errors, warnings = validate_candidate(db, book_id, payload, invoice_fields)
    if errors:
        raise LLMError("模型候选凭证未通过校验：" + "；".join(errors))

    confidence = 0.9 if not warnings else 0.6
    return {
        "voucher": payload,
        "warnings": warnings,
        "confidence": confidence,
        "usage": result.get("usage", {}),
    }


def confirm_suggestion(
    db: Session,
    *,
    doc_id: int,
    voucher_date,
    lines: list[dict],
    operator_id: int,
) -> Voucher:
    doc = db.get(AIDoc, doc_id)
    if doc is None:
        raise VoucherError("AI 解析记录不存在")
    if doc.status not in ("parsed", "suggested"):
        raise VoucherError("该记录已确认或已废弃")

    has_file = bool(doc.staging_path) and Path(doc.staging_path).exists()
    voucher = create_voucher(
        db,
        book_id=doc.book_id,
        voucher_date=voucher_date,
        lines=lines,
        operator_id=operator_id,
        attachment_count=1 if has_file else 0,
        source="ai",
    )

    if has_file:
        path = Path(doc.staging_path)
        save_attachment(
            db,
            voucher=voucher,
            content=path.read_bytes(),
            original_filename=doc.file_name or path.name,
            content_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            operator_id=operator_id,
        )
        path.unlink(missing_ok=True)

    doc.status = "confirmed"
    doc.voucher_id = voucher.id
    db.commit()
    db.refresh(voucher)
    return voucher


def discard_document(db: Session, doc_id: int) -> None:
    doc = db.get(AIDoc, doc_id)
    if doc is None:
        raise VoucherError("AI 解析记录不存在")
    if doc.status == "confirmed":
        raise VoucherError("已确认落账的记录不能废弃")
    if doc.staging_path and Path(doc.staging_path).exists():
        Path(doc.staging_path).unlink(missing_ok=True)
    doc.status = "discarded"
    doc.staging_path = ""
    db.commit()
