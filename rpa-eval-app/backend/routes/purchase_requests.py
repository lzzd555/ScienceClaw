from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from auth import get_current_user, require_roles
from database import get_db
from models import Contract, PurchaseRequest, PurchaseRequestItem, User
from schemas import PurchaseRequestCreate, PurchaseRequestItemOut, PurchaseRequestOut


router = APIRouter(dependencies=[Depends(get_current_user)])


def request_to_out(request: PurchaseRequest) -> PurchaseRequestOut:
    return PurchaseRequestOut(
        number=request.number,
        title=request.title,
        contract_number=request.contract.number,
        supplier_name=request.contract.supplier.name,
        department=request.department,
        requester=request.requester,
        status=request.status,
        total_amount=request.total_amount,
        created_at=request.created_at,
        items=[
            PurchaseRequestItemOut(
                name=item.name,
                quantity=item.quantity,
                unit_price=item.unit_price,
                cost_center=item.cost_center,
            )
            for item in request.items
        ],
    )


@router.get("", response_model=list[PurchaseRequestOut])
def list_purchase_requests(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[PurchaseRequestOut]:
    requests = db.query(PurchaseRequest).order_by(PurchaseRequest.number).all()
    return [request_to_out(request) for request in requests]


@router.post("", response_model=PurchaseRequestOut)
def create_purchase_request(
    payload: PurchaseRequestCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin", "buyer")),
) -> PurchaseRequestOut:
    existing = db.query(PurchaseRequest).filter(PurchaseRequest.number == "PR-2026-RPA-NEW-001").one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Fixed new purchase request already exists")

    contract = db.query(Contract).filter(Contract.number == payload.contract_number).one_or_none()
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found")

    total_amount = sum(item.quantity * item.unit_price for item in payload.items)
    request = PurchaseRequest(
        number="PR-2026-RPA-NEW-001",
        title=payload.title,
        contract_id=contract.id,
        department=payload.department,
        requester=payload.requester,
        status="submitted",
        total_amount=total_amount,
    )
    db.add(request)
    db.flush()
    db.add_all(
        [
            PurchaseRequestItem(
                purchase_request_id=request.id,
                name=item.name,
                quantity=item.quantity,
                unit_price=item.unit_price,
                cost_center=item.cost_center,
            )
            for item in payload.items
        ]
    )
    db.commit()
    db.refresh(request)
    return request_to_out(request)
