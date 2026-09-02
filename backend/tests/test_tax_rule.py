"""G4 价税口径注入：_tax_rule 按账套纳税人性质生成规则文本（拆不拆税由此决定）。"""

from app.ledger.ai.agent import _tax_rule
from app.ledger.book_service import create_book


def test_tax_rule_small_scale(db_session, book):
    rule = _tax_rule(book)
    assert book.taxpayer_type == "small_scale"
    assert "严禁拆税" in rule          # 进项不可抵扣，价税合计全额入费用
    assert "一般纳税人" not in rule


def test_tax_rule_general(db_session):
    general = create_book(
        db_session, name="一般纳税人测试公司", tax_no="91310000MA1K35X01A",
        start_period="2026-08", taxpayer_type="general",
    )
    rule = _tax_rule(general)
    assert "一般纳税人" in rule
    assert "销项" in rule and "进项" in rule   # 双向价税分离
    assert "严禁拆税" not in rule


def test_tax_rule_none_defaults_small():
    """拿不到账套时默认按小规模（安全回退：宁可全额入费用也不虚增进项）。"""
    rule = _tax_rule(None)
    assert "严禁拆税" in rule
