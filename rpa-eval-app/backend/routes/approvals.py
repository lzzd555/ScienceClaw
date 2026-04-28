from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from auth import get_current_user, require_roles
from database import get_db
from models import ApprovalTask, User
from schemas import ApprovalDecision, ApprovalOut


router = APIRouter(dependencies=[Depends(get_current_user)])


def approval_to_out(task: ApprovalTask) -> ApprovalOut:
    return ApprovalOut(
        task_id=task.task_id,
        purchase_order_number=task.purchase_order.number,
        assignee=task.assignee,
        status=task.status,
        decision=task.decision,
        comment=task.comment,
        updated_at=task.updated_at,
    )


@router.get("", response_model=list[ApprovalOut])
def list_approvals(
    status: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[ApprovalOut]:
    query = db.query(ApprovalTask)
    if status:
        query = query.filter(ApprovalTask.status == status)
    return [approval_to_out(task) for task in query.order_by(ApprovalTask.task_id).all()]


@router.post("/{task_id}/approve", response_model=ApprovalOut)
def approve_task(
    task_id: str,
    payload: ApprovalDecision | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin", "approver")),
) -> ApprovalOut:
    task = db.query(ApprovalTask).filter(ApprovalTask.task_id == task_id).one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Approval task not found")
    task.status = "approved"
    task.decision = "approved"
    task.comment = payload.comment if payload else "同意采购订单进入执行"
    task.updated_at = datetime.utcnow()
    task.purchase_order.status = "approved"
    db.commit()
    db.refresh(task)
    return approval_to_out(task)
