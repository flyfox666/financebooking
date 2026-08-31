from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.core.database import get_db
from app.ledger.tax import calendar as tax_calendar
from app.ledger.tax.cit import calc_cit
from app.ledger.tax.export import XLSX_MEDIA_TYPE, export_tax_workbook
from app.ledger.tax.iit import calc_iit
from app.ledger.tax.params import get_param
from app.ledger.tax.stamp import calc_stamp
from app.ledger.tax.vat import calc_vat
from app.models.book import Book
from app.models.tax import TaxParam
from app.models.user import User
from app.schemas.tax import IitCalcIn, StampCalcIn, TaxParamUpsertIn

router = APIRouter(prefix="/api/tax", tags=["tax"])


@router.get("/vat")
def get_vat(
    book_id: int,
    year: int,
    quarter: int,
    unissued_income: float = 0.0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return calc_vat(
        db,
        book_id=book_id,
        year=year,
        quarter=quarter,
        unissued_income=Decimal(str(unissued_income)),
    )


@router.get("/cit")
def get_cit(
    book_id: int,
    year: int,
    quarter: int,
    employees: int | None = None,
    assets: float | None = None,
    prepaid_prev: float = 0.0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return calc_cit(
        db,
        book_id=book_id,
        year=year,
        quarter=quarter,
        employees=employees,
        assets=Decimal(str(assets)) if assets is not None else None,
        prepaid_prev=Decimal(str(prepaid_prev)),
    )


@router.post("/stamp")
def post_stamp(
    body: StampCalcIn,
    book_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        return calc_stamp(
            db,
            book_id=book_id,
            year=body.year,
            contracts=[item.model_dump() for item in body.contracts],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/iit")
def post_iit(
    body: IitCalcIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return calc_iit(
        month=body.month,
        cumulative_income=body.cumulative_income,
        cumulative_deductions=body.cumulative_deductions,
        withheld_prev=body.withheld_prev,
    )


@router.get("/calendar")
def get_calendar(year: int | None = None, user: User = Depends(get_current_user)):
    return tax_calendar.filing_calendar(year or date.today().year)


@router.get("/reminders")
def get_reminders(
    within_days: int = 7,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return tax_calendar.upcoming_reminders(within_days=within_days)


@router.get("/params/{tax}/{name}")
def get_tax_param(
    tax: str,
    name: str,
    book_id: int,
    on_date: date | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        value = get_param(db, book_id=book_id, tax=tax, name=name, on_date=on_date)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    normalized = format(value, "f").rstrip("0").rstrip(".")
    return {"tax": tax, "name": name, "value": normalized or "0"}


@router.post("/params")
def upsert_tax_param(
    body: TaxParamUpsertIn,
    book_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if db.get(Book, book_id) is None:
        raise HTTPException(status_code=404, detail="账套不存在")
    row = (
        db.query(TaxParam)
        .filter_by(
            book_id=book_id, tax=body.tax, name=body.name, effective_from=body.effective_from
        )
        .first()
    )
    if row is None:
        row = TaxParam(
            book_id=book_id, tax=body.tax, name=body.name, effective_from=body.effective_from
        )
        db.add(row)
    row.value = body.value
    row.effective_to = body.effective_to
    db.commit()
    return {"tax": body.tax, "name": body.name, "value": str(body.value)}


@router.get("/export")
def export_tax(
    book_id: int,
    year: int,
    quarter: int,
    unissued_income: float = 0.0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    book = db.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="账套不存在")
    content = export_tax_workbook(
        db,
        book_id=book_id,
        year=year,
        quarter=quarter,
        book_name=book.name,
        unissued_income=Decimal(str(unissued_income)),
    )
    filename = f"tax-assistant-{year}Q{quarter}.xlsx"
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
