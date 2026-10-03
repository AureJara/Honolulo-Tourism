from flask import current_app, request
from marshmallow import EXCLUDE, Schema, ValidationError, fields, validate, validates, validates_schema

from honolulo_common.errors import ApiError, validation_error

from .names import InvalidName, clean_person_name
from .passwords import check_password


class _Base(Schema):
    class Meta:
        unknown = EXCLUDE


def _password_rules(pwd: str, personal: tuple[str, ...] = ()) -> None:
    problem = check_password(pwd, min_len=current_app.config["PASSWORD_MIN_LENGTH"], personal=personal)
    if problem:
        raise ValidationError(problem)


class RegisterSchema(_Base):
    first_name = fields.String(required=True)
    last_name = fields.String(required=True)
    email = fields.Email(required=True, validate=validate.Length(max=254))
    password = fields.String(required=True, load_only=True)
    accept_privacy = fields.Boolean(required=True)

    @validates("first_name")
    def _first(self, value, **kwargs):
        self._name(value)

    @validates("last_name")
    def _last(self, value, **kwargs):
        self._name(value)

    @staticmethod
    def _name(value):
        try:
            clean_person_name(value)
        except InvalidName as exc:
            raise ValidationError(str(exc)) from exc

    @validates("password")
    def _password(self, value, **kwargs):
        _password_rules(value)

    @validates_schema
    def _password_vs_person(self, data, **kwargs):
        """La contraseña no puede contener el nombre, el apellido ni parte del correo de quien se registra."""
        personal = (data.get("first_name", ""), data.get("last_name", ""), str(data.get("email", "")).split("@")[0])
        try:
            _password_rules(data["password"], personal)
        except ValidationError as exc:
            raise ValidationError(exc.messages, "password") from exc

    @validates("accept_privacy")
    def _consent(self, value, **kwargs):
        if value is not True:
            raise ValidationError("Debes aceptar la política de privacidad para crear la cuenta.")


class LoginSchema(_Base):
    email = fields.Email(required=True)
    password = fields.String(required=True, validate=validate.Length(min=1, max=128))


class TokenSchema(_Base):
    refresh_token = fields.String(required=True, validate=validate.Length(min=10, max=200))


class VerifyEmailSchema(_Base):
    email = fields.Email(required=True)
    code = fields.String(required=True, validate=validate.Regexp(r"^[0-9]{6}$", error="El código tiene 6 dígitos."))


class ResendSchema(_Base):
    email = fields.Email(required=True)


def load_json(schema: Schema) -> dict:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiError(400, "BAD_REQUEST", "Se esperaba un cuerpo JSON.")
    try:
        return schema.load(payload)
    except ValidationError as exc:
        raise validation_error(exc.normalized_messages()) from exc
