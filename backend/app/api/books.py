from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin, require_book_access
from app.core.database import get_db
from app.ledger import account_service, aux_service, book_service
from app.ledger.ai.packs import TAGS
from app.ledger.exceptions import LedgerError
from app.models.ai import AiStyleSetting
from app.models.book import Book
from app.models.user import User
from app.schemas.account import AccountCreate, AccountNode, AccountOut, AccountPatch
from app.schemas.book import AiStyleIn, BookCreate, BookOut
from app.schemas.report import OpeningSetIn

router = APIRouter(prefix="/api", tags=["books"])


@router.get("/books", response_model=list[BookOut])
def list_books(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """当前用户可见账套列表：全局 admin 全部；其他用户为被授权成员的账套。"""
    return book_service.visible_books(db, user)


@router.post("/books", response_model=BookOut, status_code=201)
def create_book(
    body: BookCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
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
    # 创建者自动成为该账套 admin 成员
    book_service.add_member(db, user_id=user.id, book_id=book.id, role="admin")
    return book


@router.get("/books/{book_id}", response_model=BookOut)
def get_book(
    book_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_book_access),
):
    book = db.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="账套不存在")
    return book


@router.get("/books/{book_id}/opening")
def get_opening(
    book_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_book_access),
):
    from sqlalchemy import select

    from app.models.report import OpeningBalance

    rows = db.scalars(
        select(OpeningBalance).where(OpeningBalance.book_id == book_id).order_by(OpeningBalance.id)
    ).all()
    return [
        {
            "account_code": row.account_code,
            "debit": str(row.debit),
            "credit": str(row.credit),
        }
        for row in rows
    ]


@router.put("/books/{book_id}/opening")
def set_opening(
    book_id: int,
    body: OpeningSetIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    try:
        return book_service.set_opening_balances(
            db, book_id=book_id, items=[item.model_dump() for item in body.items]
        )
    except LedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/books/{book_id}/ai-style")
def get_ai_style(
    book_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_book_access),
):
    if db.get(Book, book_id) is None:
        raise HTTPException(status_code=404, detail="账套不存在")
    setting = db.scalar(select(AiStyleSetting).where(AiStyleSetting.book_id == book_id))
    if setting is None:
        current = {"tags": [], "business_desc": "", "style_prompt": ""}
    else:
        import json as _json

        try:
            tags = [t for t in _json.loads(setting.tags_json or "[]") if t in TAGS]
        except (_json.JSONDecodeError, TypeError):
            tags = []
        current = {
            "tags": tags,
            "business_desc": setting.business_desc or "",
            "style_prompt": setting.style_prompt or "",
        }
    return {
        "tags": [
            {"id": key, "name": value["name"], "desc": value["desc"], "prompt": value["prompt"]}
            for key, value in TAGS.items()
        ],
        "current": current,
    }


@router.put("/books/{book_id}/ai-style")
def put_ai_style(
    book_id: int,
    body: AiStyleIn,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if db.get(Book, book_id) is None:
        raise HTTPException(status_code=404, detail="账套不存在")
    invalid = [t for t in body.tags if t not in TAGS]
    if invalid:
        raise HTTPException(status_code=400, detail=f"未知行业标签：{'、'.join(invalid)}")
    import json as _json

    setting = db.scalar(select(AiStyleSetting).where(AiStyleSetting.book_id == book_id))
    if setting is None:
        setting = AiStyleSetting(book_id=book_id)
        db.add(setting)
    setting.tags_json = _json.dumps(body.tags, ensure_ascii=False)
    setting.business_desc = body.business_desc.strip()
    setting.style_prompt = body.style_prompt.strip()
    db.commit()
    return {"tags": body.tags, "business_desc": setting.business_desc, "style_prompt": setting.style_prompt}


@router.get("/accounts", response_model=list[AccountNode])
def account_tree(
    book_id: int,
    only_active: bool = True,
    db: Session = Depends(get_db),
    user: User = Depends(require_book_access),
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


@router.patch("/accounts/{code}/aux")
def patch_account_aux(
    code: str,
    book_id: int,
    aux_types: str = "",
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    requested = [item for item in (aux_types or "").split(",") if item]
    try:
        account, warned = aux_service.set_aux_types(db, book_id=book_id, code=code, aux_types=requested)
    except LedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    response = {
        "code": account.code,
        "name": account.name,
        "aux_types": [item for item in (account.aux_types or "").split(",") if item],
        "warning": (
            "该科目已有凭证发生额：本次配置修改对新凭证生效，历史凭证保持原样，请注意核对期初与辅助余额。"
            if warned else ""
        ),
    }
    return response
