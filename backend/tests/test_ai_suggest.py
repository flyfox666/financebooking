import pytest

from app.ledger.ai.suggest import suggest_voucher, validate_candidate
from app.ledger.exceptions import LLMError

VALID_MODEL_JSON = (
    '{"voucher_date":"2026-08-06","lines":['
    '{"summary":"收到技术服务费","account_code":"1122","debit":"11300.00","credit":"0.00"},'
    '{"summary":"确认技术服务收入","account_code":"5001","debit":"0.00","credit":"11188.12"},'
    '{"summary":"计提增值税","account_code":"2221","debit":"0.00","credit":"111.88"}]}'
)


def test_suggest_success(db_session, book, monkeypatch):
    monkeypatch.setattr(
        "app.ledger.ai.suggest.gateway.chat",
        lambda db, **kwargs: {"content": VALID_MODEL_JSON, "usage": {"total_tokens": 100}},
    )
    result = suggest_voucher(
        db_session,
        book_id=book.id,
        doc_type="invoice",
        fields={"amount_total": "11300.00", "status": "normal"},
    )
    assert result["confidence"] is None
    assert result["validation_status"] == "checks_passed"
    assert result["voucher"]["lines"][0]["account_code"] == "1122"
    assert result["warnings"] == []


def test_suggest_amount_mismatch_flags_warning(db_session, book, monkeypatch):
    monkeypatch.setattr(
        "app.ledger.ai.suggest.gateway.chat",
        lambda db, **kwargs: {"content": VALID_MODEL_JSON, "usage": {}},
    )
    result = suggest_voucher(
        db_session,
        book_id=book.id,
        doc_type="invoice",
        fields={"amount_total": "99999.00", "status": "normal"},
    )
    assert result["confidence"] is None
    assert result["validation_status"] == "needs_review"
    assert any("不一致" in warning for warning in result["warnings"])


def test_suggest_rejects_unknown_account(db_session, book, monkeypatch):
    bad = (
        '{"voucher_date":"2026-08-06","lines":['
        '{"summary":"a","account_code":"9999","debit":"100.00","credit":"0.00"},'
        '{"summary":"b","account_code":"5603","debit":"0.00","credit":"100.00"}]}'
    )
    monkeypatch.setattr(
        "app.ledger.ai.suggest.gateway.chat",
        lambda db, **kwargs: {"content": bad, "usage": {}},
    )
    with pytest.raises(LLMError, match="9999 不存在"):
        suggest_voucher(db_session, book_id=book.id, doc_type="text", note="买文具100元")


def test_suggest_rejects_unbalanced(db_session, book, monkeypatch):
    unbalanced = (
        '{"voucher_date":"2026-08-06","lines":['
        '{"summary":"a","account_code":"1002","debit":"100.00","credit":"0.00"},'
        '{"summary":"b","account_code":"5603","debit":"0.00","credit":"90.00"}]}'
    )
    monkeypatch.setattr(
        "app.ledger.ai.suggest.gateway.chat",
        lambda db, **kwargs: {"content": unbalanced, "usage": {}},
    )
    with pytest.raises(LLMError, match="借贷不平衡"):
        suggest_voucher(db_session, book_id=book.id, doc_type="text", note="测试")


def test_suggest_rejects_non_json(db_session, book, monkeypatch):
    monkeypatch.setattr(
        "app.ledger.ai.suggest.gateway.chat",
        lambda db, **kwargs: {"content": "我觉得应该借银行存款贷收入", "usage": {}},
    )
    with pytest.raises(LLMError, match="合法 JSON"):
        suggest_voucher(db_session, book_id=book.id, doc_type="text", note="测试")


def test_validate_candidate_red_invoice_hint(db_session, book):
    payload = {
        "voucher_date": "2026-08-06",
        "lines": [
            {"summary": "a", "account_code": "1122", "debit": "-100.00", "credit": "0.00"},
            {"summary": "b", "account_code": "5001", "debit": "0.00", "credit": "-100.00"},
        ],
    }
    errors, warnings = validate_candidate(
        db_session, book.id, payload, {"amount_total": "-100.00", "status": "red"}
    )
    assert errors == []


# ---------- G8 发票关联与补录（confirm_suggestion → _link_invoice）----------

import json
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.ledger.ai.suggest import confirm_suggestion
from app.models.ai import AIDoc
from app.models.tax import Invoice

G8_LINES = [
    {"summary": "费用", "account_code": "5602", "debit": "100.00", "credit": "0"},
    {"summary": "银行付款", "account_code": "1002", "debit": "0", "credit": "100.00"},
]


def _invoice_doc(db_session, book, fields, source_kind="pdf", note=None):
    payload = {"note": note} if note is not None else fields
    doc = AIDoc(
        book_id=book.id, doc_type="invoice", source_kind=source_kind,
        fields_json=json.dumps(payload, ensure_ascii=False), status="parsed",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    return doc


def _confirm(db_session, doc, mama_user):
    return confirm_suggestion(
        db_session, doc_id=doc.id, voucher_date="2026-08-05",
        lines=[dict(line, debit=str(-Decimal(line["debit"])), credit=str(-Decimal(line["credit"]))) for line in G8_LINES] if json.loads(doc.fields_json).get("status") == "red" else G8_LINES, operator_id=mama_user.id,
    )


def test_link_invoice_existing_no(db_session, book, mama_user):
    """台账已有该发票号 → 落账后回写 invoice.voucher_id。"""
    inv = Invoice(
        book_id=book.id, kind="purchase", invoice_no="12345678",
        invoice_date=date(2026, 8, 1), amount_total=Decimal("100.00"),
    )
    db_session.add(inv)
    db_session.commit()

    doc = _invoice_doc(db_session, book, {"invoice_no": "12345678"})
    voucher = _confirm(db_session, doc, mama_user)

    db_session.refresh(inv)
    assert inv.voucher_id == voucher.id
    db_session.refresh(doc)
    assert doc.status == "confirmed" and doc.voucher_id == voucher.id


def test_link_invoice_creates_from_pdf_fields(db_session, book, mama_user):
    """台账无此号 + PDF 解析 → 自动补录；购方税号=账套 → 判定为进项。"""
    fields = {
        "invoice_no": "87654321", "invoice_date": "2026-08-02",
        "buyer_tax_no": book.tax_no, "buyer_name": book.name,
        "seller_name": "某客户公司", "goods_name": "技术服务费",
        "amount_total": "100.00", "amount_excl": "99.00", "tax_amount": "1.00",
    }
    doc = _invoice_doc(db_session, book, fields)
    voucher = _confirm(db_session, doc, mama_user)

    created = db_session.scalar(
        select(Invoice).where(Invoice.book_id == book.id, Invoice.invoice_no == "87654321")
    )
    assert created is not None
    assert created.kind == "purchase"
    assert created.voucher_id == voucher.id
    assert created.amount_total == Decimal("100.00")


def test_link_invoice_text_no_create(db_session, book, mama_user):
    """纯文本路径：可从描述提取发票号关联，但字段不足不补录台账。"""
    doc = _invoice_doc(db_session, book, {}, source_kind="text", note="发票号：12345678 的费用100元")
    _confirm(db_session, doc, mama_user)

    assert db_session.scalar(
        select(Invoice).where(Invoice.book_id == book.id, Invoice.invoice_no == "12345678")
    ) is None


def test_link_invoice_red_negative(db_session, book, mama_user):
    """红字发票补录：金额取负数、status=red。"""
    fields = {
        "invoice_no": "11112222", "invoice_date": "2026-08-03",
        "amount_total": "-100.00", "status": "red", "buyer_tax_no": book.tax_no,
    }
    doc = _invoice_doc(db_session, book, fields)
    _confirm(db_session, doc, mama_user)

    created = db_session.scalar(
        select(Invoice).where(Invoice.book_id == book.id, Invoice.invoice_no == "11112222")
    )
    assert created is not None
    assert created.status == "red"
    assert created.amount_total == Decimal("-100.00")
