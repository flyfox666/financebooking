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
    assert result["confidence"] == 0.9
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
    assert result["confidence"] == 0.6
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
