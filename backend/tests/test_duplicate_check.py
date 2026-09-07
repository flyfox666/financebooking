"""G2 重复检测护栏：实质性重复导向——发票号金标准 + 同日同金额同科目同往来才算疑似。"""

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


def _posted_voucher(
    db_session, book, mama_user, auditor_user, post_flow,
    total="100.00", vdate="2026-08-01", account="5602", contact_id=None,
):
    voucher = voucher_service.create_voucher(
        db_session, book_id=book.id, voucher_date=vdate, attachment_count=1,
        lines=[
            {"summary": "测试费用", "account_code": account, "debit": total, "credit": "0", "contact_id": contact_id},
            {"summary": "银行付款", "account_code": "1002", "debit": "0", "credit": total},
        ],
        operator_id=mama_user.id,
    )
    post_flow(voucher, mama_user, auditor_user)
    return voucher


def _candidate(total="100.00", vdate="2026-08-01", account="5602", contact_id=None):
    return {
        "voucher_date": vdate,
        "lines": [
            {"summary": "测试费用", "account_code": account, "debit": total, "credit": "0", "contact_id": contact_id},
            {"summary": "银行付款", "account_code": "1002", "debit": "0", "credit": total},
        ],
    }


def test_hard_check_duplicate_hit(db_session, book, mama_user, auditor_user, post_flow):
    """同日 + 同金额 + 同科目（同笔单据二次录入的特征）→ 返回含警告标记与凭证号的警告文本。"""
    _posted_voucher(db_session, book, mama_user, auditor_user, post_flow, vdate="2026-08-01")
    warning = _hard_check_duplicate(db_session, book.id, _candidate(vdate="2026-08-01"))
    assert warning is not None
    assert DUP_WARN_MARK in warning
    assert "记字第" in warning


def test_hard_check_duplicate_amount_only_not_hit(db_session, book, mama_user, auditor_user, post_flow):
    """仅金额相同不算重复（重复金额是正常业务）：不同日期 / 不同科目 / 不同往来 / 金额不同 → 放行。"""
    _posted_voucher(db_session, book, mama_user, auditor_user, post_flow, vdate="2026-08-01")
    # 金额不同
    assert _hard_check_duplicate(db_session, book.id, _candidate(total="999.00")) is None
    # 同金额但不同日期（如月度房租：每月同额同科目，日期不同是正常业务）
    assert _hard_check_duplicate(db_session, book.id, _candidate(vdate="2026-08-10")) is None
    assert _hard_check_duplicate(db_session, book.id, _candidate(vdate="2026-12-01")) is None
    # 同金额同日但科目类别不同（同为 100 元，一个是费用一个是资产）
    assert _hard_check_duplicate(db_session, book.id, _candidate(account="1601")) is None


def test_hard_check_duplicate_diff_contacts_not_hit(
    db_session, book, mama_user, auditor_user, post_flow, contacts_pair
):
    """同日同金额同科目但往来对象不同（同一科目向两家供应商各付 100）→ 放行。"""
    _posted_voucher(
        db_session, book, mama_user, auditor_user, post_flow,
        vdate="2026-08-01", contact_id=contacts_pair["customer"].id,
    )
    warning = _hard_check_duplicate(
        db_session, book.id,
        _candidate(vdate="2026-08-01", contact_id=contacts_pair["supplier"].id),
    )
    assert warning is None


def test_hard_check_duplicate_same_contacts_hit(
    db_session, book, mama_user, auditor_user, post_flow, contacts_pair
):
    """同日同金额同科目且往来对象一致（同一单据二次上传）→ 命中。"""
    _posted_voucher(
        db_session, book, mama_user, auditor_user, post_flow,
        vdate="2026-08-01", contact_id=contacts_pair["customer"].id,
    )
    warning = _hard_check_duplicate(
        db_session, book.id,
        _candidate(vdate="2026-08-01", contact_id=contacts_pair["customer"].id),
    )
    assert warning is not None
    assert DUP_WARN_MARK in warning


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
    """聊天内容不授权重复入账；必须对服务器风险指纹单独确认。"""
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
    assert _dup_already_confirmed(warned) is False
