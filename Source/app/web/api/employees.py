"""Сотрудники клиники."""
from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.services import employees as svc
from app.services import reviews as reviews_service
from app.web.deps import Manager, ManagerConn

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/employees", tags=["employees"])


class EmployeeIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    unit_id: str | None = None
    staff_position_id: str | None = None
    role_category: str | None = None
    specialty: str | None = None
    work_phone: str | None = Field(default=None, max_length=32)
    work_email: str | None = Field(default=None, max_length=200)
    hired_at: date | None = None
    status: str = "active"


class EmployeePatch(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    unit_id: str | None = None
    staff_position_id: str | None = None
    role_category: str | None = None
    specialty: str | None = None
    work_phone: str | None = None
    work_email: str | None = None
    hired_at: date | None = None
    status: str | None = None
    note: str | None = None


@router.get("")
async def listing(
    conn: ManagerConn,
    _: Manager,
    unit_id: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    search: str | None = Query(default=None, max_length=100),
):
    return {
        "employees": await svc.listing(
            conn, unit_id=unit_id, status=status_filter, search=search
        ),
        "summary": await svc.summary(conn),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(payload: EmployeeIn, conn: ManagerConn, user: Manager):
    if payload.status not in ("active", "onboarding"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Нового сотрудника можно принять только как active или onboarding"
        )
    employee = await svc.create(
        conn,
        clinic_id=user.clinic_id,
        full_name=payload.full_name,
        unit_id=payload.unit_id,
        staff_position_id=payload.staff_position_id,
        role_category=payload.role_category,
        specialty=payload.specialty,
        work_phone=payload.work_phone,
        work_email=payload.work_email,
        hired_at=payload.hired_at.isoformat() if payload.hired_at else None,
        status=payload.status,
    )
    log.info("принят сотрудник %s в клинику %s", employee["id"], user.clinic_id)
    return employee


@router.patch("/{employee_id}")
async def patch(employee_id: str, payload: EmployeePatch, conn: ManagerConn, _: Manager):
    fields = payload.model_dump(exclude_unset=True)
    if "hired_at" in fields and fields["hired_at"] is not None:
        fields["hired_at"] = fields["hired_at"].isoformat()
    # Увольнение — отдельное действие: у него своя дата и свои следствия,
    # и делать его «одним из полей» значит однажды уволить человека случайно.
    if fields.get("status") == "dismissed":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Для увольнения используйте POST /{id}/dismiss"
        )
    updated = await svc.update(conn, employee_id, fields)
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Сотрудник не найден или нечего менять")
    return updated


@router.post("/{employee_id}/dismiss")
async def dismiss(employee_id: str, conn: ManagerConn, _: Manager):
    result = await svc.dismiss(conn, employee_id)
    if result is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Сотрудник не найден или уже уволен"
        )
    log.info("уволен сотрудник %s", employee_id)
    return result


@router.post("/{employee_id}/qr")
async def issue_qr(employee_id: str, conn: ManagerConn, _: Manager):
    target = await reviews_service.ensure_employee_target(conn, employee_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Сотрудник не найден")
    return target
