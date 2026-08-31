from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.ledger import balances, export_service, report_service
from app.ledger.export_service import EXPORT_BUILDERS, XLSX_MEDIA_TYPE
from app.models.user import User

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/trial-balance")
def get_trial_balance(
    book_id: int,
    period: str,
    complete: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return balances.trial_balance(db, book_id=book_id, period=period, complete=complete)


@router.get("/aux-balance")
def get_aux_balance(
    book_id: int,
    period: str,
    account_code: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.ledger.aux_service import aux_trial_balance

    return aux_trial_balance(db, book_id=book_id, period=period, account_code=account_code)


@router.get("/general-ledger")
def get_general_ledger(
    book_id: int, period: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return balances.general_ledger(db, book_id=book_id, period=period)


@router.get("/detail-ledger")
def get_detail_ledger(
    book_id: int,
    account_code: str,
    period_from: str,
    period_to: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return balances.detail_ledger(
        db, book_id=book_id, account_code=account_code, period_from=period_from, period_to=period_to
    )


@router.get("/balance-sheet")
def get_balance_sheet(
    book_id: int, period: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return report_service.balance_sheet(db, book_id=book_id, period=period)


@router.get("/income-statement")
def get_income_statement(
    book_id: int, period: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return report_service.income_statement(db, book_id=book_id, period=period)


@router.get("/cash-flow")
def get_cash_flow(
    book_id: int, period: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    from app.ledger.cashflow import cash_flow

    return cash_flow(db, book_id=book_id, period=period)


@router.get("/export")
def export_report(
    book_id: int,
    period: str,
    report: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if report not in EXPORT_BUILDERS:
        raise HTTPException(status_code=404, detail=f"不支持的报表导出类型：{report}")
    builder, exporter = EXPORT_BUILDERS[report]
    payload = builder(db, book_id=book_id, period=period)
    content = exporter(payload)
    filename = f"{report}-{period}.xlsx"
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
