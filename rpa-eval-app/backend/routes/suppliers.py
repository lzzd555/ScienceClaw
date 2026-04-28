from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from auth import get_current_user, require_roles
from database import get_db
from models import Supplier, User
from schemas import SupplierOut, SupplierUpdate


router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("", response_model=list[SupplierOut])
def list_suppliers(
    status: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[Supplier]:
    query = db.query(Supplier)
    if status:
        query = query.filter(Supplier.status == status)
    return query.order_by(Supplier.number).all()


@router.patch("/{number}", response_model=SupplierOut)
def update_supplier(
    number: str,
    payload: SupplierUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin", "buyer")),
) -> Supplier:
    supplier = db.query(Supplier).filter(Supplier.number == number).one_or_none()
    if supplier is None:
        raise HTTPException(status_code=404, detail="Supplier not found")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(supplier, field, value)
    if supplier.contact_person and supplier.contact_phone:
        supplier.status = "active"
    db.commit()
    db.refresh(supplier)
    return supplier
