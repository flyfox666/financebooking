import re

from sqlalchemy.orm import Session

from app.ledger.account_service import seed_accounts
from app.ledger.exceptions import BookError
from app.models.book import Book

PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
TAXPAYER_TYPES = {"small_scale", "general"}
ENTITY_TYPES = {"company", "individual", "partnership"}


def create_book(
    db: Session,
    *,
    name: str,
    tax_no: str = "",
    taxpayer_type: str = "small_scale",
    entity_type: str = "company",
    start_period: str,
) -> Book:
    name = (name or "").strip()
    if not (2 <= len(name) <= 128):
        raise BookError("企业名称长度须在 2~128 字之间")
    if taxpayer_type not in TAXPAYER_TYPES:
        raise BookError("纳税人身份不合法")
    if entity_type not in ENTITY_TYPES:
        raise BookError("主体类型不合法")
    tax_no = (tax_no or "").strip()
    if tax_no and not re.fullmatch(r"[0-9A-Z]{18}", tax_no):
        raise BookError("统一社会信用代码须为 18 位数字或大写字母")
    if not PERIOD_RE.match(start_period or ""):
        raise BookError("启用期间格式应为 YYYY-MM")
    book = Book(
        name=name,
        tax_no=tax_no,
        taxpayer_type=taxpayer_type,
        entity_type=entity_type,
        start_period=start_period,
    )
    db.add(book)
    db.flush()
    seed_accounts(db, book.id)
    db.refresh(book)
    return book
