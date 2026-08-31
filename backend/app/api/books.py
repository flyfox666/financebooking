from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.core.database import get_db
from app.ledger import account_service, book_service
from app.ledger.exceptions import LedgerError
from app.models.book import Book
from app.models.user import User
from app.schemas.account import AccountCreate, AccountNode, AccountOut, AccountPatch
from app.schemas.book import BookCreate, BookOut

router = APIRouter(prefix="/api", tags=["books"])


@router.post("/books", response_model=BookOut, status_code=201)
def create_book(
    body: BookCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        book = book_service.create_book(
            db,
            name=body.name,
            tax_no=body.tax_no,
            taxpayer_type=body.taxpayer_type,
            entity_type=body.entity_type,
            start_period=body.start_period,
        )
    except LedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return book


@router.get("/books/{book_id}", response_model=BookOut)
def get_book(
    book_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    book = db.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="账套不存在")
    return book


@router.get("/accounts", response_model=list[AccountNode])
def account_tree(
    book_id: int,
    only_active: bool = True,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if db.get(Book, book_id) is None:
        raise HTTPException(status_code=404, detail="账套不存在")
    return account_service.get_account_tree(db, book_id=book_id, only_active=only_active)


@router.post("/accounts", response_model=AccountOut, status_code=201)
def create_account(
    body: AccountCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    try:
        account = account_service.create_detail_account(
            db,
            book_id=body.book_id,
            parent_code=body.parent_code,
            code=body.code,
            name=body.name,
        )
    except LedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return account


@router.patch("/accounts/{code}", response_model=AccountOut)
def patch_account(
    code: str,
    body: AccountPatch,
    book_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    try:
        account = account_service.set_account_active(
            db, book_id=book_id, code=code, is_active=body.is_active
        )
    except LedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return account
