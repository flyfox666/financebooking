import pytest
from sqlalchemy import func, select

from app.ledger.account_service import (
    create_detail_account,
    get_account_tree,
    seed_accounts,
    set_account_active,
)
from app.ledger.accounts_catalog import ACCOUNT_CATALOG
from app.ledger.exceptions import AccountError
from app.models.account import Account


def count_accounts(db_session, book_id):
    return db_session.scalar(
        select(func.count()).select_from(Account).where(Account.book_id == book_id)
    )


def test_seed_creates_66_accounts(db_session, book):
    assert count_accounts(db_session, book.id) == 66


def test_seed_is_idempotent(db_session, book):
    assert seed_accounts(db_session, book.id) == 0
    assert count_accounts(db_session, book.id) == 66


def test_default_active_flags(db_session, book):
    bank = db_session.scalar(
        select(Account).where(Account.book_id == book.id, Account.code == "1002")
    )
    raw_material = db_session.scalar(
        select(Account).where(Account.book_id == book.id, Account.code == "1403")
    )
    assert bank.is_active is True
    assert raw_material.is_active is False


def test_detail_account_inherits_category_and_direction(db_session, book):
    child = create_detail_account(
        db_session, book_id=book.id, parent_code="5602", code="5602.01", name="办公费"
    )
    assert child.level == 2
    assert child.category == "pnl"
    assert child.direction == 1
    assert child.is_preset is False
    assert child.is_leaf is True
    parent = db_session.scalar(
        select(Account).where(Account.book_id == book.id, Account.code == "5602")
    )
    assert parent.is_leaf is False


def test_detail_code_prefix_enforced(db_session, book):
    with pytest.raises(AccountError):
        create_detail_account(
            db_session, book_id=book.id, parent_code="5602", code="5603.01", name="错位"
        )


def test_duplicate_detail_code_rejected(db_session, book):
    create_detail_account(
        db_session, book_id=book.id, parent_code="5602", code="5602.01", name="办公费"
    )
    with pytest.raises(AccountError):
        create_detail_account(
            db_session, book_id=book.id, parent_code="5602", code="5602.01", name="重复"
        )


def test_three_level_depth_allowed_four_rejected(db_session, book):
    create_detail_account(
        db_session, book_id=book.id, parent_code="5602", code="5602.01", name="办公费"
    )
    child = create_detail_account(
        db_session, book_id=book.id, parent_code="5602.01", code="5602.01.01", name="办公用品"
    )
    assert child.level == 3
    with pytest.raises(AccountError):
        create_detail_account(
            db_session,
            book_id=book.id,
            parent_code="5602.01.01",
            code="5602.01.01.01",
            name="越级",
        )


def test_cannot_disable_parent_with_active_children(db_session, book):
    create_detail_account(
        db_session, book_id=book.id, parent_code="5602", code="5602.01", name="办公费"
    )
    with pytest.raises(AccountError):
        set_account_active(db_session, book_id=book.id, code="5602", is_active=False)
    set_account_active(db_session, book_id=book.id, code="5602.01", is_active=False)
    parent = set_account_active(db_session, book_id=book.id, code="5602", is_active=False)
    assert parent.is_active is False


def test_tree_nests_children_and_filters_active(db_session, book):
    create_detail_account(
        db_session, book_id=book.id, parent_code="5602", code="5602.01", name="办公费"
    )
    full_tree = get_account_tree(db_session, book_id=book.id, only_active=False)
    assert len(full_tree) == 66
    by_code = {node["code"]: node for node in full_tree}
    assert by_code["5602"]["children"][0]["code"] == "5602.01"

    active_tree = get_account_tree(db_session, book_id=book.id, only_active=True)
    expected_active = sum(1 for row in ACCOUNT_CATALOG if row[4])
    assert len(active_tree) == expected_active
    assert "1403" not in {node["code"] for node in active_tree}
