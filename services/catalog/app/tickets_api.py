"""Rutas HTTP de la boletería: precio (público), compra y «mis boletos» (con sesión), validación y ventas (administrador)."""

from __future__ import annotations

import re

from flask import Blueprint, g, jsonify
from marshmallow import fields, validate

from . import tickets
from .permissions import admin_only, signed_in
from .responses import no_store
from .schemas import BaseSchema, load_body

bp = Blueprint("tickets", __name__)
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
CODE_RE = re.compile(r"^HN-[A-Z0-9]{8}$", re.IGNORECASE)


class PurchaseSchema(BaseSchema):
    place = fields.String(required=True, validate=validate.Regexp(SLUG_RE))
    date = fields.Date(required=True)
    quantity = fields.Integer(required=True, strict=True, validate=validate.Range(min=1, max=tickets.MAX_PER_ORDER))
    payment_method = fields.String(required=True, validate=validate.OneOf(tickets.METHODS))


class CodeSchema(BaseSchema):
    code = fields.String(required=True, validate=validate.Regexp(CODE_RE))


@bp.get("/api/v1/tickets/config")
def config():
    return jsonify(tickets.config())


@bp.post("/api/v1/tickets")
@signed_in
def buy():
    data = load_body(PurchaseSchema())
    name = (g.user.get("name") or g.user.get("email") or "Visitante")
    order = tickets.purchase(data["place"], data["date"], data["quantity"], data["payment_method"], name, g.user["sub"])
    return no_store(jsonify(order)), 201


@bp.get("/api/v1/tickets/mine")
@signed_in
def mine():
    return no_store(jsonify({"items": tickets.mine(g.user["sub"])}))


@bp.post("/api/v1/tickets/cancel")
@signed_in
def cancel():
    data = load_body(CodeSchema())
    return no_store(jsonify(tickets.cancel(data["code"], g.user["sub"])))


@bp.post("/api/v1/admin/tickets/validate")
@admin_only
def validate_ticket():
    data = load_body(CodeSchema())
    return no_store(jsonify(tickets.validate(data["code"])))


@bp.get("/api/v1/admin/tickets/summary")
@admin_only
def summary():
    return no_store(jsonify(tickets.summary()))
