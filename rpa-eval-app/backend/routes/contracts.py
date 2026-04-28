from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from models import Contract, Supplier, User
from schemas import ContractOut


router = APIRouter(dependencies=[Depends(get_current_user)])


def contract_to_out(contract: Contract) -> ContractOut:
    return ContractOut(
        number=contract.number,
        title=contract.title,
        contract_type=contract.contract_type,
        status=contract.status,
        supplier_number=contract.supplier.number,
        supplier_name=contract.supplier.name,
        amount=contract.amount,
        currency=contract.currency,
        owner_department=contract.owner_department,
        start_date=contract.start_date,
        end_date=contract.end_date,
        compliance_clause=contract.compliance_clause,
    )


@router.get("", response_model=list[ContractOut])
def list_contracts(
    status: str | None = None,
    contract_type: str | None = None,
    supplier: str | None = None,
    number: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[ContractOut]:
    query = db.query(Contract).join(Supplier)
    if status:
        query = query.filter(Contract.status == status)
    if contract_type:
        query = query.filter(Contract.contract_type == contract_type)
    if supplier:
        supplier_like = f"%{supplier}%"
        query = query.filter((Supplier.number == supplier) | (Supplier.name.like(supplier_like)))
    if number:
        query = query.filter(Contract.number == number)
    return [contract_to_out(contract) for contract in query.order_by(Contract.number).all()]
