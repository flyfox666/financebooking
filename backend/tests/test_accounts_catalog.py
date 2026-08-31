from collections import Counter

from app.ledger.accounts_catalog import ACCOUNT_CATALOG


def test_catalog_has_exactly_66_accounts():
    assert len(ACCOUNT_CATALOG) == 66


def test_catalog_codes_unique():
    codes = [row[0] for row in ACCOUNT_CATALOG]
    assert len(codes) == len(set(codes))


def test_catalog_codes_are_four_digit():
    for code, *_ in ACCOUNT_CATALOG:
        assert len(code) == 4
        assert code.isdigit()


def test_catalog_category_counts():
    counts = Counter(row[2] for row in ACCOUNT_CATALOG)
    assert counts["asset"] == 32
    assert counts["liability"] == 12
    assert counts["equity"] == 5
    assert counts["cost"] == 5
    assert counts["pnl"] == 12


def test_key_account_names_match_standard():
    by_code = {row[0]: row for row in ACCOUNT_CATALOG}
    assert by_code["5001"][1] == "主营业务收入"
    assert by_code["5401"][1] == "主营业务成本"
    assert by_code["5403"][1] == "税金及附加"
    assert by_code["5602"][1] == "管理费用"
    assert by_code["5603"][1] == "财务费用"
    assert by_code["4301"][1] == "研发支出"
    assert by_code["3001"][1] == "实收资本"
    assert by_code["2221"][1] == "应交税费"


def test_key_account_directions():
    by_code = {row[0]: row for row in ACCOUNT_CATALOG}
    assert by_code["1002"][3] == 1
    assert by_code["2221"][3] == -1
    assert by_code["3001"][3] == -1
    assert by_code["4301"][3] == 1
    assert by_code["5001"][3] == -1
    assert by_code["5401"][3] == 1
    assert by_code["5602"][3] == 1


def test_contra_accounts_have_credit_direction():
    by_code = {row[0]: row for row in ACCOUNT_CATALOG}
    assert by_code["1602"][3] == -1
    assert by_code["1622"][3] == -1
    assert by_code["1702"][3] == -1
