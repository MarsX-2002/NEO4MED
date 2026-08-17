"""Структура клиники: дерево подразделений и штатные единицы."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.services import reviews as reviews_service
from app.services import structure as svc
from app.web.deps import Manager, ManagerConn

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/structure", tags=["structure"])


class UnitIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_id: str | None = None
    district: str | None = None


class UnitPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    parent_id: str | None = None
    move: bool = False          # перенос отличаем явно: parent_id=None это тоже перенос в корень


class PositionIn(BaseModel):
    unit_id: str | None = None
    title: str = Field(min_length=1, max_length=200)
    role_category: str
    specialty: str | None = None
    seats: int = Field(default=1, ge=1, le=999)


class SeatsIn(BaseModel):
    seats: int = Field(ge=1, le=999)


@router.get("")
async def get_tree(conn: ManagerConn, _: Manager):
    return {
        "units": await svc.tree(conn),
        "positions": await svc.positions(conn),
    }


@router.get("/dictionaries")
async def get_dictionaries(conn: ManagerConn, _: Manager):
    return await svc.dictionaries(conn)


@router.post("/units", status_code=status.HTTP_201_CREATED)
async def create_unit(payload: UnitIn, conn: ManagerConn, user: Manager):
    unit = await svc.create_unit(
        conn,
        clinic_id=user.clinic_id,
        name=payload.name,
        parent_id=payload.parent_id,
        district=payload.district,
    )
    log.info("создан узел %s клиникой %s", unit["id"], user.clinic_id)
    return unit


@router.patch("/units/{unit_id}")
async def patch_unit(unit_id: str, payload: UnitPatch, conn: ManagerConn, _: Manager):
    result: dict = {}
    if payload.name is not None:
        renamed = await svc.rename_unit(conn, unit_id, payload.name)
        if renamed is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Подразделение не найдено")
        result |= renamed
    if payload.move:
        try:
            moved = await svc.move_unit(conn, unit_id, payload.parent_id)
        except ValueError as e:
            raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
        if moved is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Подразделение не найдено")
        result |= moved
    if not result:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нечего менять")
    return result


@router.delete("/units/{unit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_unit(unit_id: str, conn: ManagerConn, _: Manager):
    problem = await svc.delete_unit(conn, unit_id)
    if problem:
        # 409, а не 400: запрос корректен, просто состояние не позволяет.
        raise HTTPException(status.HTTP_409_CONFLICT, f"Нельзя удалить: {problem}")


@router.post("/units/{unit_id}/qr")
async def issue_unit_qr(unit_id: str, conn: ManagerConn, _: Manager):
    """Выдаёт QR-цель для узла. Повторный вызов возвращает ту же — код
    печатается один раз и меняться не должен."""
    target = await reviews_service.ensure_unit_target(conn, unit_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Подразделение не найдено")
    return target


@router.post("/positions", status_code=status.HTTP_201_CREATED)
async def create_position(payload: PositionIn, conn: ManagerConn, user: Manager):
    return await svc.create_position(
        conn,
        clinic_id=user.clinic_id,
        unit_id=payload.unit_id,
        title=payload.title,
        role_category=payload.role_category,
        specialty=payload.specialty,
        seats=payload.seats,
    )


@router.patch("/positions/{position_id}")
async def patch_position(position_id: str, payload: SeatsIn, conn: ManagerConn, _: Manager):
    updated = await svc.update_position_seats(conn, position_id, payload.seats)
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Штатная единица не найдена")
    return updated
