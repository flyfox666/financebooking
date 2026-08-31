from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.database import get_db
from app.ledger.exceptions import LedgerError, LLMError
from app.ledger.llm import gateway
from app.models.llm import LLMProvider
from app.models.user import User
from app.schemas.llm import ChatDebugIn, ProviderCreate, ProviderOut, ProviderUpdate

router = APIRouter(prefix="/api/llm", tags=["llm"])


def _to_out(provider: LLMProvider) -> dict:
    data = ProviderOut.model_validate(provider).model_dump()
    data["key_masked"] = gateway.mask_key(provider.api_key)
    return data


@router.get("/providers", response_model=list[ProviderOut])
def list_providers(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    rows = db.scalars(select(LLMProvider).order_by(LLMProvider.id)).all()
    return [_to_out(row) for row in rows]


@router.post("/providers", response_model=ProviderOut, status_code=201)
def create_provider(
    body: ProviderCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if db.scalar(select(LLMProvider).where(LLMProvider.name == body.name)):
        raise HTTPException(status_code=409, detail="同名模型服务已存在")
    provider = LLMProvider(**body.model_dump())
    db.add(provider)
    db.commit()
    db.refresh(provider)
    if body.is_default:
        gateway.set_default(db, provider.id)
    return _to_out(provider)


@router.patch("/providers/{provider_id}", response_model=ProviderOut)
def update_provider(
    provider_id: int,
    body: ProviderUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    provider = db.get(LLMProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="模型服务不存在")
    updates = body.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(provider, key, value)
    db.commit()
    db.refresh(provider)
    return _to_out(provider)


@router.delete("/providers/{provider_id}", status_code=204)
def delete_provider(
    provider_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    provider = db.get(LLMProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="模型服务不存在")
    was_default = provider.is_default
    db.delete(provider)
    db.commit()
    if was_default:
        next_provider = db.scalar(
            select(LLMProvider).where(LLMProvider.enabled.is_(True)).order_by(LLMProvider.id)
        )
        if next_provider is not None:
            gateway.set_default(db, next_provider.id)


@router.post("/providers/{provider_id}/default", response_model=ProviderOut)
def make_default(
    provider_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    try:
        gateway.set_default(db, provider_id)
    except LLMError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _to_out(db.get(LLMProvider, provider_id))


@router.post("/providers/{provider_id}/test")
def test_provider(
    provider_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    try:
        return gateway.test_provider(db, provider_id)
    except LedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/chat")
def chat_debug(
    body: ChatDebugIn,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    try:
        return gateway.chat(
            db,
            provider_id=body.provider_id,
            messages=body.messages,
            json_mode=body.json_mode,
            max_tokens=body.max_tokens,
        )
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
