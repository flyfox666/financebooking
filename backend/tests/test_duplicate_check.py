"""G2 重复检测护栏：_hard_check_duplicate 硬兜底 + check_duplicate 工具的「仅已入账算重」。"""

import json
from datetime import date
from decimal import Decimal

from app.ledger import voucher_service
from app.ledger.ai.agent import (
    DUP_WARN_MARK,
    _dup_already_confirmed,
    _hard_check_duplicate,
    execute_tool,
)
from app.models.tax import Invoice


def _posted_voucher(db_session, book, mama_user, auditor_user, post_flow, total="100.00", vdate="2026-08-01"):
    voucher = voucher_service.create_voucher(
        db_session, book_id=book.id, voucher_date=vdate, attachment_count=1,
        lines=[
            {"summary": "测试费用", "account_code": "5602", "debit": total, "credit": "0"},
            {"summary": "银行付款", "account_code": "1002", "debit": "0", "credit": total},
        ],
        operator_id=mama_user.id,
    )
    post_flow(voucher, mama_user, auditor_user)
    return voucher


def _candidate(total="100.00", vdate="2026-08-10"):
    return {
        "voucher_date": vdate,
        "lines": [
            {"summary": "测试费用", "account_code": "5602", "debit": total, "credit": "0"},
            {"summary": "银行付款", "account_code": "1002", "debit": "0", "credit": total},
        ],
    }


def test_hard_check_duplicate_hit(db_session, book, mama_user, auditor_user, post_flow):
    """相同金额 + 30 天窗口内已有凭证 → 返回含警告标记与凭证号的警告文本。"""
    _posted_voucher(db_session, book, mama_user, auditor_user, post_flow, vdate="2026-08-01")
    warning = _hard_check_duplicate(db_session, book.id, _candidate(vdate="2026-08-10"))
    assert warning is not None
    assert DUP_WARN_MARK in warning
    assert "记字第" in warning


def test_hard_check_duplicate_no_hit(db_session, book, mama_user, auditor_user, post_flow):
    """金额不同或超出 30 天窗口 → 放行（None），不误伤。"""
    _posted_voucher(db_session, book, mama_user, auditor_user, post_flow, vdate="2026-08-01")
    # 金额不同
    assert _hard_check_duplicate(db_session, book.id, _candidate(total="999.00")) is None
    # 日期超出 ±30 天窗口
    assert _hard_check_duplicate(db_session, book.id, _candidate(vdate="2026-12-01")) is None


def test_dup_invoice_only_counts_posted(db_session, book, mama_user, auditor_user, post_flow):
    """发票号查重只算已入账（voucher_id 非空）：已导入未生成凭证的发票不算重复。"""
    voucher = _posted_voucher(db_session, book, mama_user, auditor_user, post_flow)
    unlinked = Invoice(
        book_id=book.id, kind="purchase", invoice_no="11111111",
        invoice_date=date(2026, 8, 1), amount_total=Decimal("100.00"),
    )
    linked = Invoice(
        book_id=book.id, kind="purchase", invoice_no="22222222",
        invoice_date=date(2026, 8, 2), amount_total=Decimal("200.00"),
        voucher_id=voucher.id,
    )
    db_session.add_all([unlinked, linked])
    db_session.commit()

    # 已导入但未生成凭证 → 不算重复（发票驱动记账的正常起点）
    r1 = json.loads(execute_tool(db_session, book.id, "check_duplicate", {"invoice_no": "11111111"}))
    assert r1["invoice_no_match"] == []
    assert r1["has_duplicate"] is False

    # 已关联凭证 → 命中
    r2 = json.loads(execute_tool(db_session, book.id, "check_duplicate", {"invoice_no": "22222222"}))
    assert r2["invoice_no_match"] and r2["invoice_no_match"][0]["posted"] is True
    assert r2["has_duplicate"] is True


def test_dup_already_confirmed_not_looping():
    """历史消息已含重复警告 → 视为用户已确认，不再二次拦截（防死循环）。"""
    plain = [
        {"role": "user", "content": "记一笔费用"},
        {"role": "assistant", "content": "已生成凭证草稿"},
    ]
    assert _dup_already_confirmed(plain) is False

    warned = [
        {"role": "user", "content": "记一笔费用"},
        {"role": "assistant", "content": f"⚠️ {DUP_WARN_MARK}：已存在相同金额的凭证……"},
        {"role": "user", "content": "确认入账"},
    ]
    assert _dup_already_confirmed(warned) is True
