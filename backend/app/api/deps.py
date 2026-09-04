from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="登录状态无效",
        headers={"WWW-Authenticate": "Bearer"},
    )
    payload = decode_access_token(token)
    if payload is None:
        raise credentials_error
    username = payload.get("sub")
    if not username:
        raise credentials_error
    user = db.scalar(select(User).where(User.username == username))
    if user is None or not user.is_active:
        raise credentials_error
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user


def require_auditor_or_admin(user: User = Depends(get_current_user)) -> User:
    if user.role not in ("auditor", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="需要审核或管理员权限"
        )
    return user


def require_book_access(
    book_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> User:
    """账套访问门槛：全局 admin 或账套成员放行，否则 403（多租户隔离钩子）。"""
    from app.ledger import book_service

    if not book_service.user_can_access(db, user, book_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该账套")
    return user
