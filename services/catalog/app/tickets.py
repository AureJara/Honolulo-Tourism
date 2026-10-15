"""Boletería: compra de entradas (S/ 5 por persona), «Mis boletos», anulación y validación en la puerta.

El pago es SIMULADO (Yape, Plin, tarjeta o efectivo en puerta): no se piden ni se guardan datos de tarjeta y no se
contacta a ninguna pasarela. Para cobrar de verdad habría que contratar una (p. ej. Niubiz, Izipay) y sustituir
``_charge``; el resto del flujo (orden, código, validación) no cambia.
"""

from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta, timezone

import sqlalchemy as sa

from honolulo_common.errors import ApiError

from . import service
from .extensions import db
from .models import Place, TicketOrder
from .visits import PERU, today_pe

PRICE_CENTS = 500                       # S/ 5.00 por persona
MAX_PER_ORDER = 20
ADVANCE_DAYS = 90                       # hasta cuántos días antes se puede comprar
METHODS = ("yape", "plin", "tarjeta", "efectivo")
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"        # sin 0/O/1/I para dictarlo sin confusión


def config() -> dict:
    return {"price_cents": PRICE_CENTS, "currency": "PEN", "max_per_order": MAX_PER_ORDER,
            "advance_days": ADVANCE_DAYS, "methods": list(METHODS), "simulated_payment": True}


def _new_code() -> str:
    for _ in range(10):
        code = "HN-" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        if db.session.execute(db.select(TicketOrder.id).where(TicketOrder.code == code)).first() is None:
            return code
    raise ApiError(503, "SERVICE_UNAVAILABLE", "No se pudo generar el código del boleto. Inténtalo nuevamente.")


def _charge(method: str, total_cents: int) -> None:
    """Punto de integración con una pasarela de pago real. Hoy el cobro es simulado y siempre se aprueba."""


def _dump(order: TicketOrder, place: Place) -> dict:
    return {"code": order.code, "place": place.slug, "place_name": place.name, "buyer_name": order.buyer_name,
            "visit_date": order.visit_date.isoformat(), "quantity": order.quantity,
            "unit_price_cents": order.unit_price_cents, "total_cents": order.total_cents,
            "payment_method": order.payment_method, "status": order.status,
            "created_at": order.created_at.isoformat() if order.created_at else None,
            "used_at": order.used_at.isoformat() if order.used_at else None, "simulated_payment": True}


def _field_error(field: str, message: str) -> ApiError:
    return ApiError(422, "VALIDATION_ERROR", "Revisa los campos marcados.", errors=[{"field": field, "message": message}])


def purchase(slug: str, day: date, quantity: int, method: str, buyer_name: str, user_id: str,
             today: date | None = None) -> dict:
    today = today or today_pe()
    if day < today:
        raise _field_error("date", "Elige hoy o una fecha futura.")
    if day > today + timedelta(days=ADVANCE_DAYS):
        raise _field_error("date", f"Solo se venden boletos hasta {ADVANCE_DAYS} días antes de la visita.")
    if not 1 <= quantity <= MAX_PER_ORDER:
        raise _field_error("quantity", f"Compra entre 1 y {MAX_PER_ORDER} boletos por operación.")
    if method not in METHODS:
        raise _field_error("payment_method", "Medio de pago no válido.")
    place = service.get_public(slug)
    total = PRICE_CENTS * quantity
    _charge(method, total)
    order = TicketOrder(code=_new_code(), place_id=place.id, user_id=user_id, buyer_name=buyer_name.strip()[:100],
                        visit_date=day, quantity=quantity, unit_price_cents=PRICE_CENTS, total_cents=total,
                        payment_method=method, status="pagado")
    db.session.add(order)
    db.session.commit()
    return _dump(order, place)


def mine(user_id: str, limit: int = 30) -> list[dict]:
    rows = db.session.execute(
        sa.select(TicketOrder, Place).join(Place, Place.id == TicketOrder.place_id)
        .where(TicketOrder.user_id == user_id).order_by(TicketOrder.created_at.desc()).limit(limit)).all()
    return [_dump(o, p) for o, p in rows]


def _find(code: str) -> tuple[TicketOrder, Place]:
    row = db.session.execute(
        sa.select(TicketOrder, Place).join(Place, Place.id == TicketOrder.place_id)
        .where(TicketOrder.code == code.strip().upper())).first()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "No existe un boleto con ese código.")
    return row[0], row[1]


def cancel(code: str, user_id: str, today: date | None = None) -> dict:
    """Anula un boleto propio que aún no se usó y cuya fecha de visita no es hoy ni pasada. (Simulado: sin reembolso real.)"""
    today = today or today_pe()
    order, place = _find(code)
    if order.user_id != user_id:
        raise ApiError(404, "NOT_FOUND", "No existe un boleto con ese código.")        # no revela boletos ajenos
    if order.status != "pagado":
        raise ApiError(409, "CONFLICT", "Este boleto ya fue usado o anulado.")
    if order.visit_date <= today:
        raise ApiError(409, "CONFLICT", "Solo se pueden anular boletos de fechas futuras.")
    order.status = "anulado"
    db.session.commit()
    return _dump(order, place)


def validate(code: str, today: date | None = None, now: datetime | None = None) -> dict:
    """Validación en la puerta (administrador): marca el boleto como usado. Solo vale el día de la visita."""
    today = today or today_pe()
    order, place = _find(code)
    if order.status == "anulado":
        raise ApiError(409, "CONFLICT", "Boleto anulado: no es válido.")
    if order.status == "usado":
        raise ApiError(409, "CONFLICT", f"Boleto ya usado el {order.used_at.astimezone(PERU):%d/%m/%Y a las %H:%M}." if order.used_at
                       else "Boleto ya usado.")
    if order.visit_date != today:
        raise ApiError(409, "CONFLICT", f"Este boleto es para el {order.visit_date:%d/%m/%Y}, no para hoy.")
    order.status, order.used_at = "usado", now or datetime.now(timezone.utc)
    db.session.commit()
    return _dump(order, place)


def summary(today: date | None = None) -> dict:
    """Ventas para el administrador: hoy, últimos 30 días, próximos 7 días y por lugar (sin contar anulados)."""
    today = today or today_pe()
    live = TicketOrder.status != "anulado"

    def totals(*conds):
        n, q, c = db.session.execute(sa.select(
            sa.func.count(TicketOrder.id), sa.func.coalesce(sa.func.sum(TicketOrder.quantity), 0),
            sa.func.coalesce(sa.func.sum(TicketOrder.total_cents), 0)).where(live, *conds)).one()
        return {"orders": int(n), "tickets": int(q), "revenue_cents": int(c)}

    created_day = sa.func.date(TicketOrder.created_at)
    upcoming = db.session.execute(
        sa.select(TicketOrder.visit_date, sa.func.sum(TicketOrder.quantity)).where(
            live, TicketOrder.visit_date >= today, TicketOrder.visit_date < today + timedelta(days=7))
        .group_by(TicketOrder.visit_date).order_by(TicketOrder.visit_date)).all()
    by_place = db.session.execute(
        sa.select(Place.name, sa.func.sum(TicketOrder.quantity), sa.func.sum(TicketOrder.total_cents))
        .join(Place, Place.id == TicketOrder.place_id).where(live)
        .group_by(Place.name).order_by(sa.func.sum(TicketOrder.quantity).desc())).all()
    today_used = db.session.execute(sa.select(sa.func.coalesce(sa.func.sum(TicketOrder.quantity), 0)).where(
        TicketOrder.status == "usado", TicketOrder.visit_date == today)).scalar_one()
    return {"today": today.isoformat(), "sold_today": totals(created_day == today),
            "sold_30_days": totals(created_day >= today - timedelta(days=30)),
            "sold_total": totals(), "entering_today": totals(TicketOrder.visit_date == today),
            "used_today": int(today_used),
            "upcoming": [{"date": d.isoformat(), "tickets": int(q)} for d, q in upcoming],
            "by_place": [{"place_name": n, "tickets": int(q), "revenue_cents": int(c)} for n, q, c in by_place]}
