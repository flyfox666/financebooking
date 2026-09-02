from decimal import Decimal

from app.ledger.cashflow import cash_flow
from app.ledger.voucher_service import create_voucher


def _post(db_session, book, mama_user, auditor_user, post_flow, date, lines):
    voucher = create_voucher(
        db_session, book_id=book.id, voucher_date=date, attachment_count=1,
        lines=lines, operator_id=mama_user.id,
    )
    post_flow(voucher, mama_user, auditor_user)
    return voucher


def test_cash_flow_classification(db_session, book, mama_user, auditor_user, post_flow):
    _post(db_session, book, mama_user, auditor_user, post_flow, "2026-08-01", [
        {"summary": "实缴出资", "account_code": "1002", "debit": "500000.00", "credit": "0"},
        {"summary": "实收资本", "account_code": "3001", "debit": "0", "credit": "500000.00"},
    ])
    _post(db_session, book, mama_user, auditor_user, post_flow, "2026-08-05", [
        {"summary": "收款", "account_code": "1002", "debit": "11300.00", "credit": "0"},
        {"summary": "收入", "account_code": "5001", "debit": "0", "credit": "11188.12"},
        {"summary": "增值税", "account_code": "2221", "debit": "0", "credit": "111.88"},
    ])
    _post(db_session, book, mama_user, auditor_user, post_flow, "2026-08-06", [
        {"summary": "提现", "account_code": "1001", "debit": "2000.00", "credit": "0"},
        {"summary": "银行转出", "account_code": "1002", "debit": "0", "credit": "2000.00"},
    ])
    _post(db_session, book, mama_user, auditor_user, post_flow, "2026-08-07", [
        {"summary": "购设备", "account_code": "1601", "debit": "12000.00", "credit": "0"},
        {"summary": "付款", "account_code": "1002", "debit": "0", "credit": "12000.00"},
    ])
    _post(db_session, book, mama_user, auditor_user, post_flow, "2026-08-10", [
        {"summary": "发工资", "account_code": "2211", "debit": "5000.00", "credit": "0"},
        {"summary": "付款", "account_code": "1002", "debit": "0", "credit": "5000.00"},
    ])
    _post(db_session, book, mama_user, auditor_user, post_flow, "2026-08-11", [
        {"summary": "缴税", "account_code": "2221", "debit": "300.00", "credit": "0"},
        {"summary": "付款", "account_code": "1002", "debit": "0", "credit": "300.00"},
    ])
    _post(db_session, book, mama_user, auditor_user, post_flow, "2026-08-12", [
        {"summary": "付房租", "account_code": "5602", "debit": "8000.00", "credit": "0"},
        {"summary": "付款", "account_code": "1002", "debit": "0", "credit": "8000.00"},
    ])

    report = cash_flow(db_session, book_id=book.id, period="2026-08")
    by_name = {row["name"]: row for row in report["rows"]}
    assert by_name["销售商品、提供劳务收到的现金"]["month"] == "11300.00"
    assert by_name["吸收投资收到的现金"]["month"] == "500000.00"
    assert by_name["购建固定资产、无形资产和其他长期资产支付的现金"]["month"] == "12000.00"
    assert by_name["支付给职工以及为职工支付的现金"]["month"] == "5000.00"
    assert by_name["支付的各项税费"]["month"] == "300.00"
    assert by_name["支付其他与经营活动有关的现金"]["month"] == "8000.00"

    operating_net = Decimal(by_name["一、经营活动产生的现金流量净额"]["month"])
    assert operating_net == Decimal("-2000.00")
    assert by_name["四、现金及现金等价物净增加额"]["month"] == "486000.00"
    assert by_name["期末现金及现金等价物余额"]["month"] == "486000.00"


# ---------- G1 现金流量行级标注（cf_item）----------


def _create(db_session, book, mama_user, lines, date="2026-08-05"):
    return create_voucher(
        db_session, book_id=book.id, voucher_date=date, attachment_count=1,
        lines=lines, operator_id=mama_user.id,
    )


def test_cf_item_valid_kept(db_session, book, mama_user):
    """现金行带合法 cf_item → 原样持久化到 voucher_line。"""
    voucher = _create(db_session, book, mama_user, [
        {"summary": "收款", "account_code": "1002", "debit": "1130.00", "credit": "0", "cf_item": "sales"},
        {"summary": "收入", "account_code": "5001", "debit": "0", "credit": "1118.81"},
        {"summary": "税", "account_code": "2221", "debit": "0", "credit": "11.19"},
    ])
    assert voucher.lines[0].cf_item == "sales"


def test_cf_item_invalid_on_cash_line_falls_back(db_session, book, mama_user):
    """现金行 cf_item 非法 → 按「对方最大行科目」映射兜底（收入科目 → sales）。"""
    voucher = _create(db_session, book, mama_user, [
        {"summary": "收款", "account_code": "1002", "debit": "1130.00", "credit": "0", "cf_item": "瞎写的"},
        {"summary": "收入", "account_code": "5001", "debit": "0", "credit": "1118.81"},
        {"summary": "税", "account_code": "2221", "debit": "0", "credit": "11.19"},
    ])
    assert voucher.lines[0].cf_item == "sales"


def test_cf_item_missing_on_cash_line_falls_back(db_session, book, mama_user):
    """现金行缺 cf_item → 同样兜底，绝不为 None（报表永不缺数）。"""
    voucher = _create(db_session, book, mama_user, [
        {"summary": "付款", "account_code": "1002", "debit": "0", "credit": "12000.00"},
        {"summary": "购设备", "account_code": "1601", "debit": "12000.00", "credit": "0"},
    ])
    assert voucher.lines[0].cf_item == "capex"


def test_cf_item_cleared_on_non_cash_line(db_session, book, mama_user):
    """非现金行即便提交了 cf_item → 落库前被清除。"""
    voucher = _create(db_session, book, mama_user, [
        {"summary": "收款", "account_code": "1002", "debit": "1130.00", "credit": "0"},
        {"summary": "收入", "account_code": "5001", "debit": "0", "credit": "1118.81", "cf_item": "sales"},
        {"summary": "税", "account_code": "2221", "debit": "0", "credit": "11.19"},
    ])
    assert voucher.lines[0].cf_item == "sales"  # 现金行兜底标注
    assert voucher.lines[1].cf_item is None      # 非现金行清除


def test_one_voucher_multi_cf_items(db_session, book, mama_user, auditor_user, post_flow):
    """一张凭证两行现金（收款+付款）→ 各自标注不同流量项，现金流量表正确拆分。"""
    voucher = _create(db_session, book, mama_user, [
        {"summary": "收款", "account_code": "1002", "debit": "1130.00", "credit": "0", "cf_item": "sales"},
        {"summary": "确认收入", "account_code": "5001", "debit": "0", "credit": "1118.81"},
        {"summary": "计提增值税", "account_code": "2221", "debit": "0", "credit": "11.19"},
        {"summary": "缴税", "account_code": "1002", "debit": "0", "credit": "130.00", "cf_item": "taxes"},
        {"summary": "转出未交增值税", "account_code": "2221", "debit": "130.00", "credit": "0"},
    ])
    post_flow(voucher, mama_user, auditor_user)

    report = cash_flow(db_session, book_id=book.id, period="2026-08")
    by_name = {row["name"]: row for row in report["rows"]}
    assert by_name["销售商品、提供劳务收到的现金"]["month"] == "1130.00"
    assert by_name["支付的各项税费"]["month"] == "130.00"
    assert by_name["四、现金及现金等价物净增加额"]["month"] == "1000.00"
    assert by_name["期末现金及现金等价物余额"]["month"] == "1000.00"
