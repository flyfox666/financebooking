from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin, require_book_access
from app.core.database import get_db
from app.ledger.exceptions import LedgerError
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


def cit_inputs(
    employees: Decimal | None = Query(None, ge=0, le=100000000),
    assets: Decimal | None = Query(None, ge=0, le=100000000000000),
    prepaid_prev: Decimal = Query(Decimal("0"), ge=0, le=100000000000000),
    industry_eligible: bool | None = None,
    adjustment_net: Decimal = Query(Decimal("0"), ge=-100000000000000, le=100000000000000),
    adjustments_confirmed: bool = False,
):
    return dict(employees=employees, assets=assets, prepaid_prev=prepaid_prev,
                industry_eligible=industry_eligible, adjustment_net=adjustment_net,
                adjustments_confirmed=adjustments_confirmed)


@router.get("/vat")
def get_vat(
    book_id: int,
    year: int,
    quarter: int,
    unissued_income: float = 0.0,
    db: Session = Depends(get_db),
    user: User = Depends(require_book_access),
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
    year: int = Query(ge=2000, le=2098),
    quarter: int = Query(ge=1, le=4),
    inputs: dict = Depends(cit_inputs),
    db: Session = Depends(get_db),
    user: User = Depends(require_book_access),
):
    try:
        return calc_cit(db, book_id=book_id, year=year, quarter=quarter, **inputs)
    except (LedgerError, KeyError) as exc:
        raise HTTPException(400, detail=str(exc))


@router.post("/stamp")
def post_stamp(
    body: StampCalcIn,
    book_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_book_access),
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
def get_calendar(year: int | None = Query(None, ge=2000, le=2098), book_id: int | None = None,
                 vat_frequency: Literal["monthly", "quarterly"] = "quarterly",
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    entity_type = "company"
    if book_id is not None:
        require_book_access(book_id, db, user)
        book = db.get(Book, book_id)
        if book is None:
            raise HTTPException(404, detail="账套不存在")
        entity_type = book.entity_type
    return tax_calendar.filing_calendar(year or date.today().year, vat_frequency=vat_frequency, entity_type=entity_type)


@router.get("/reminders")
def get_reminders(
    within_days: int = Query(7, ge=1, le=366),
    book_id: int | None = None,
    vat_frequency: Literal["monthly", "quarterly"] = "quarterly",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    entity_type = "company"
    if book_id is not None:
        require_book_access(book_id, db, user)
        book = db.get(Book, book_id)
        if book is None:
            raise HTTPException(404, detail="账套不存在")
        entity_type = book.entity_type
    return tax_calendar.upcoming_reminders(within_days=within_days, vat_frequency=vat_frequency, entity_type=entity_type)


@router.get("/params/{tax}/{name}")
def get_tax_param(
    tax: str,
    name: str,
    book_id: int,
    on_date: date | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_book_access),
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
    year: int = Query(ge=2000, le=2098),
    quarter: int = Query(ge=1, le=4),
    unissued_income: float = 0.0,
    inputs: dict = Depends(cit_inputs),
    vat_frequency: Literal["monthly", "quarterly"] = "quarterly",
    db: Session = Depends(get_db),
    user: User = Depends(require_book_access),
):
    book = db.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="账套不存在")
    if book.taxpayer_type != "small_scale":
        raise HTTPException(400, detail="当前组合导出仅支持小规模纳税人季度辅助汇总")
    try:
        content = export_tax_workbook(
            db,
            book_id=book_id,
            year=year,
            quarter=quarter,
            book_name=book.name,
            unissued_income=Decimal(str(unissued_income)),
            cit_inputs=inputs,
            vat_frequency=vat_frequency,
            entity_type=book.entity_type,
        )
    except (LedgerError, KeyError) as exc:
        raise HTTPException(400, detail=str(exc))
    filename = f"tax-assistant-{year}Q{quarter}.xlsx"
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
