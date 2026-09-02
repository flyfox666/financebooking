from fastapi import APIRouter, Body, Depends, HTTPException, Response
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


@router.get("/period-summary")
def get_period_summary(
    book_id: int, period: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return report_service.period_summary(db, book_id=book_id, period=period)


@router.get("/template-check")
def get_template_check(
    book_id: int, period: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """映射体检：报表行→科目映射全景、未映射科目、脏引用、恒等式校验。"""
    return report_service.template_check(db, book_id=book_id, period=period)


@router.put("/template-row")
def update_template_row(
    book_id: int,
    report: str,
    key: str,
    formula: list = Body(..., embed=False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """编辑指定报表行的取数科目映射（formula 为 [[科目编码, ±1], ...]）。"""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可编辑报表模板")
    try:
        return report_service.update_template_row(
            db, book_id=book_id, report=report, key=key, formula=formula
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/template-reset")
def reset_template(
    book_id: int,
    report: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """恢复指定报表的默认取数模板（覆盖当前账套该报表全部公式）。"""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可重置报表模板")
    try:
        rows = report_service.reset_template(db, book_id=book_id, report=report)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "report": report, "rows": rows}


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
