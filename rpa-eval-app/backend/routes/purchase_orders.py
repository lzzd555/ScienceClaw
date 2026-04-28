from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from auth import get_current_user, require_roles
from database import get_db
from models import PurchaseOrder, PurchaseRequest, User
from schemas import PurchaseOrderOut


router = APIRouter(dependencies=[Depends(get_current_user)])


def order_to_out(order: PurchaseOrder) -> PurchaseOrderOut:
    return PurchaseOrderOut(
        number=order.number,
        request_number=order.purchase_request.number,
        supplier_number=order.supplier.number,
        supplier_name=order.supplier.name,
        status=order.status,
        priority=order.priority,
        total_amount=order.total_amount,
        created_at=order.created_at,
    )


@router.get("", response_model=list[PurchaseOrderOut])
def list_purchase_orders(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[PurchaseOrderOut]:
    orders = db.query(PurchaseOrder).order_by(PurchaseOrder.number).all()
    return [order_to_out(order) for order in orders]


@router.post("/from-request/{request_number}", response_model=PurchaseOrderOut)
def generate_purchase_order(
    request_number: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin", "buyer")),
) -> PurchaseOrderOut:
    existing = db.query(PurchaseOrder).filter(PurchaseOrder.number == "PO-2026-RPA-NEW-001").one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Fixed new purchase order already exists")

    request = db.query(PurchaseRequest).filter(PurchaseRequest.number == request_number).one_or_none()
    if request is None:
        raise HTTPException(status_code=404, detail="Purchase request not found")

    order = PurchaseOrder(
        number="PO-2026-RPA-NEW-001",
        purchase_request_id=request.id,
        supplier_id=request.contract.supplier_id,
        status="pending_approval",
        priority="high",
        total_amount=request.total_amount,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order_to_out(order)
