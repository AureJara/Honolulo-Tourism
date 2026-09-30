from __future__ import annotations

import re

from flask import request
from marshmallow import EXCLUDE, Schema, ValidationError, fields, validate, validates_schema

from honolulo_common.errors import ApiError, validation_error

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")      # conserva \n y \t


def _clean_text(value: str, *, multiline: bool = False) -> str:
    value = _CONTROL.sub("", value).strip()
    if not multiline:
        value = re.sub(r"\s+", " ", value)
    return value


class _Base(Schema):
    class Meta:
        unknown = EXCLUDE


class PlaceFields(_Base):
    name = fields.String(validate=validate.Length(min=3, max=120))
    description = fields.String(validate=validate.Length(min=20, max=2000))
    difficulty = fields.String(allow_none=True, validate=validate.OneOf(["easy", "moderate", "hard"]))
    hike_minutes = fields.Integer(allow_none=True, strict=True, validate=validate.Range(min=1, max=600))
    depth_label = fields.String(allow_none=True, validate=validate.Length(min=1, max=40))
    depth_min_m = fields.Float(allow_none=True, validate=validate.Range(min=0, max=99.9))
    depth_max_m = fields.Float(allow_none=True, validate=validate.Range(min=0, max=99.9))
    status = fields.String(validate=validate.OneOf(["draft", "published", "archived"]))
    sort_order = fields.Integer(strict=True, validate=validate.Range(min=0, max=1000))

    @validates_schema
    def _depth_order(self, data, **kwargs):
        low, high = data.get("depth_min_m"), data.get("depth_max_m")
        if low is not None and high is not None and low > high:
            raise ValidationError("La profundidad mínima no puede superar la máxima.", "depth_min_m")


class PlaceCreateSchema(PlaceFields):
    name = fields.String(required=True, validate=validate.Length(min=3, max=120))
    description = fields.String(required=True, validate=validate.Length(min=20, max=2000))


class PhotoUpdateSchema(_Base):
    alt_text = fields.String(validate=validate.Length(min=3, max=200))
    is_cover = fields.Boolean()
    sort_order = fields.Integer(strict=True, validate=validate.Range(min=0, max=1000))


def load_body(schema: Schema, *, partial: bool = False) -> dict:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiError(400, "BAD_REQUEST", "Se esperaba un cuerpo JSON.")
    try:
        data = schema.load(payload, partial=partial)
    except ValidationError as exc:
        raise validation_error(exc.normalized_messages()) from exc
    if partial and not data:
        raise ApiError(422, "VALIDATION_ERROR", "No hay cambios que aplicar.",
                       errors=[{"field": "_", "message": "Envía al menos un campo para modificar."}])
    for key in ("name", "depth_label", "alt_text"):
        if isinstance(data.get(key), str):
            data[key] = _clean_text(data[key])
    if isinstance(data.get("description"), str):
        data["description"] = _clean_text(data["description"], multiline=True)
    # Tras limpiar puede haber quedado por debajo del mínimo (p. ej. solo espacios)
    for key, minimum in (("name", 3), ("description", 20), ("alt_text", 3)):
        if key in data and len(data[key]) < minimum:
            raise validation_error({key: [f"Debe tener al menos {minimum} caracteres."]})
    return data
