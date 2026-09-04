from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.database import get_db
from app.core.security import hash_password
from app.models.book import UserBook
from app.models.user import User
from app.schemas.auth import UserCreate, UserOut, UserPatch

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    return db.scalars(select(User).order_by(User.id)).all()


@router.post("", response_model=UserOut, status_code=201)
def create_user(
    body: UserCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    exists = db.scalar(select(User).where(User.username == body.username))
    if exists:
        raise HTTPException(status_code=409, detail="用户名已存在")
    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        display_name=body.display_name,
        role=body.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserOut)
def patch_user(
    user_id: int,
    body: UserPatch,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    if body.is_active is False and user.id == admin.id:
        raise HTTPException(status_code=400, detail="不能停用自己的账号")
    if (body.role and body.role != "admin") or body.is_active is False:
        remaining = db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.id != user.id, User.role == "admin", User.is_active.is_(True))
        )
        if user.role == "admin" and user.is_active and not remaining:
            raise HTTPException(status_code=400, detail="不能取消或停用最后一个管理员")

    if body.display_name is not None:
        user.display_name = body.display_name
    if body.role is not None:
        user.role = body.role
    if body.password is not None:
        user.password_hash = hash_password(body.password)
    if body.is_active is not None:
        user.is_active = body.is_active
    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=204)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="不能删除自己的账号")
    if user.role == "admin":
        remaining = db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.id != user.id, User.role == "admin", User.is_active.is_(True))
        )
        if not remaining:
            raise HTTPException(status_code=400, detail="不能删除最后一个管理员")
    # 级联清理账套成员关系（防孤儿授权）；若该用户是某账套唯一 admin 成员，
    # 账套仍可被全局 admin 管理（admin 天然可见全部），故不阻断删除。
    db.query(UserBook).filter(UserBook.user_id == user.id).delete(synchronize_session=False)
    db.delete(user)
    db.commit()
