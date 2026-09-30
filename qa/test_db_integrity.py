"""Integridad de datos en PostgreSQL: las restricciones deben rechazar datos inválidos aunque falle la
validación de la aplicación (defensa en profundidad). Se ejecutan contra las bases reales de desarrollo
dentro de transacciones que siempre se revierten."""

import datetime as dt
import uuid

import psycopg
import pytest

from conftest import ROOT


@pytest.fixture(scope="module")
def uri():
    import pgserver
    return pgserver.get_server(str(ROOT / ".pgdata"), cleanup_mode=None).get_uri().rsplit("/", 1)[0]


def connect(uri, db):
    return psycopg.connect(f"{uri}/{db}")


NOW = dt.datetime.now(dt.timezone.utc)


def rejected(uri, db, sql, params=()):
    """Devuelve True si la base rechaza la sentencia (y deja todo como estaba)."""
    with connect(uri, db) as conn:
        try:
            conn.execute(sql, params)
        except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation, psycopg.errors.NotNullViolation,
                psycopg.errors.ForeignKeyViolation, psycopg.errors.InvalidTextRepresentation,
                psycopg.errors.NumericValueOutOfRange) as exc:
            conn.rollback()
            return type(exc).__name__
        conn.rollback()
        return None


OBS = ("insert into weather_observations(location_code, observed_at, temperature_c, precipitation_mm, condition, "
       "source, provider, fetched_at{extra}) values('x', now(), 20, 0, 'clear', %s, 'p', now(){vals})")


@pytest.mark.parametrize("extra,vals,params,expected", [
    (", humidity_pct", ", 120", ("current",), "CheckViolation"),
    (", humidity_pct", ", -1", ("current",), "CheckViolation"),
    (", rain_probability_pct", ", 101", ("current",), "CheckViolation"),
    (", cloud_cover_pct", ", 150", ("current",), "CheckViolation"),
    (", wind_kph", ", -5", ("current",), "CheckViolation"),
    ("", "", ("otra",), "CheckViolation"),
], ids=["humedad>100", "humedad<0", "prob.lluvia>100", "nubosidad>100", "viento<0", "origen-invalido"])
def test_weather_observations_reject_out_of_range_values(uri, extra, vals, params, expected):
    assert rejected(uri, "weather_db", OBS.format(extra=extra, vals=vals), params) == expected


def test_weather_observations_reject_negative_precipitation_and_null_required(uri):
    sql = "insert into weather_observations(location_code, observed_at, temperature_c, precipitation_mm, condition, source, provider, fetched_at) values('x', now(), 20, -1, 'clear', 'current', 'p', now())"
    assert rejected(uri, "weather_db", sql) == "CheckViolation"
    sql = "insert into weather_observations(location_code, observed_at, precipitation_mm, condition, source, provider, fetched_at) values('x', now(), 0, 'clear', 'current', 'p', now())"
    assert rejected(uri, "weather_db", sql) == "NotNullViolation"                         # temperatura obligatoria


def test_the_same_observation_cannot_be_stored_twice_but_current_and_hourly_can_coexist(uri):
    with connect(uri, "weather_db") as conn:
        ins = "insert into weather_observations(location_code, observed_at, temperature_c, precipitation_mm, condition, source, provider, fetched_at) values('qa', %s, 20, 0, 'clear', %s, 'p', now())"
        conn.execute(ins, (NOW, "current"))
        conn.execute(ins, (NOW, "hourly"))                                                # misma hora, otra fuente: permitido
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(ins, (NOW, "current"))
        conn.rollback()


FC = ("insert into weather_forecasts(location_code, issued_at, target_at, temperature_c, precipitation_mm, condition, provider"
      "{extra}) values('qa', %s, %s, 20, 0, 'clear', 'p'{vals})")


def test_forecast_constraints_and_append_only_uniqueness(uri):
    assert rejected(uri, "forecast_db", FC.format(extra=", humidity_pct", vals=", 130"), (NOW, NOW)) == "CheckViolation"
    assert rejected(uri, "forecast_db", FC.format(extra=", rain_probability_pct", vals=", -3"), (NOW, NOW)) == "CheckViolation"
    with connect(uri, "forecast_db") as conn:
        conn.execute(FC.format(extra="", vals=""), (NOW, NOW + dt.timedelta(hours=3)))
        with pytest.raises(psycopg.errors.UniqueViolation):                                # misma emisión y hora objetivo
            conn.execute(FC.format(extra="", vals=""), (NOW, NOW + dt.timedelta(hours=3)))
        conn.rollback()
        conn.execute(FC.format(extra="", vals=""), (NOW, NOW + dt.timedelta(hours=3)))
        conn.execute(FC.format(extra="", vals=""), (NOW + dt.timedelta(hours=1), NOW + dt.timedelta(hours=3)))   # otra emisión: permitido
        conn.rollback()


def test_only_one_scoring_formula_can_be_active_and_evaluations_cascade(uri):
    sql = ("insert into scoring_parameters(weights, tolerances, rain_prob_threshold_pct, rain_observed_threshold_mm, labels, is_active) "
           "values('{}', '{}', 50, 0.1, '[]', true)")
    assert rejected(uri, "forecast_db", sql) == "UniqueViolation"                         # ya existe la versión activa
    with connect(uri, "forecast_db") as conn:
        fid = conn.execute("insert into weather_forecasts(location_code, issued_at, target_at, temperature_c, precipitation_mm, condition, provider) values('qa', %s, %s, 20, 0, 'clear', 'p') returning id", (NOW, NOW + dt.timedelta(hours=2))).fetchone()[0]
        version = conn.execute("select version from scoring_parameters where is_active").fetchone()[0]
        conn.execute("insert into forecast_evaluations(forecast_id, params_version, accuracy_score, accuracy_label, variables, evaluated_at) values(%s, %s, 88, 'x', '{}', now())", (fid, version))
        with pytest.raises(psycopg.errors.UniqueViolation):                                # una evaluación por pronóstico y versión
            conn.execute("insert into forecast_evaluations(forecast_id, params_version, accuracy_score, accuracy_label, variables, evaluated_at) values(%s, %s, 90, 'x', '{}', now())", (fid, version))
        conn.rollback()
        conn.execute("insert into weather_forecasts(id, location_code, issued_at, target_at, temperature_c, precipitation_mm, condition, provider) values(-7, 'qa', %s, %s, 20, 0, 'clear', 'p')", (NOW, NOW))
        conn.execute("insert into forecast_evaluations(forecast_id, params_version, accuracy_score, accuracy_label, variables, evaluated_at) values(-7, %s, 10, 'x', '{}', now())", (version,))
        conn.execute("delete from weather_forecasts where id=-7")
        assert conn.execute("select count(*) from forecast_evaluations where forecast_id=-7").fetchone()[0] == 0
        conn.rollback()


@pytest.mark.parametrize("score", [-0.1, 100.5, 1000])
def test_accuracy_score_must_be_between_0_and_100(uri, score):
    with connect(uri, "forecast_db") as conn:
        fid = conn.execute("insert into weather_forecasts(location_code, issued_at, target_at, temperature_c, precipitation_mm, condition, provider) values('qa', %s, %s, 20, 0, 'clear', 'p') returning id", (NOW, NOW + dt.timedelta(hours=2))).fetchone()[0]
        version = conn.execute("select version from scoring_parameters where is_active").fetchone()[0]
        with pytest.raises((psycopg.errors.CheckViolation, psycopg.errors.NumericValueOutOfRange)):
            conn.execute("insert into forecast_evaluations(forecast_id, params_version, accuracy_score, accuracy_label, variables, evaluated_at) values(%s, %s, %s, 'x', '{}', now())", (fid, version, score))
        conn.rollback()


USR = ("insert into users(id, email, email_canonical, password_hash, first_name, last_name{extra}) "
       "values(%s, %s, %s, 'h', 'Ana', 'Ríos'{vals})")


def test_users_constraints(uri):
    """El usuario base se crea dentro de la transacción (y se revierte): no depende de cuentas del entorno."""
    def violates(conn, exc, sql, params=()):
        with pytest.raises(exc):
            with conn.transaction():                       # punto de guardado: el error no aborta la transacción externa
                conn.execute(sql, params)

    with connect(uri, "auth_db") as conn:
        conn.execute(USR.format(extra="", vals=""), (uuid.uuid4(), "qa.base@gmail.com", "qabase@gmail.com"))
        violates(conn, psycopg.errors.CheckViolation, USR.format(extra=", role", vals=", 'superadmin'"),
                 (uuid.uuid4(), "otro1@qa.test", "otro1@qa.test"))                       # rol inexistente
        violates(conn, psycopg.errors.UniqueViolation, USR.format(extra="", vals=""),
                 (uuid.uuid4(), "qa.base@gmail.com", "distinto@qa.test"))                # mismo correo
        violates(conn, psycopg.errors.UniqueViolation, USR.format(extra="", vals=""),
                 (uuid.uuid4(), "q.a.base+otra@gmail.com", "qabase@gmail.com"))          # alias de Gmail = misma cuenta
        violates(conn, psycopg.errors.NotNullViolation,
                 "insert into users(id, email, email_canonical, password_hash, first_name) "
                 "values(gen_random_uuid(), 'n@qa.test', 'n@qa.test', 'h', 'Ana')")      # apellido obligatorio
        conn.rollback()


def test_passwords_and_tokens_are_never_stored_in_clear(uri):
    with connect(uri, "auth_db") as conn:
        hashes = [r[0] for r in conn.execute("select password_hash from users")]
        assert hashes and all(h.startswith(("scrypt:", "pbkdf2:")) and len(h) > 60 for h in hashes)
        tokens = [r[0] for r in conn.execute("select token_hash from refresh_tokens")]
        assert all(len(t) == 64 and all(c in "0123456789abcdef" for c in t) for t in tokens)          # SHA-256, no el token
        codes = [r[0] for r in conn.execute("select code_hash from email_verifications")]
        assert all(len(c) == 64 and not c.isdigit() for c in codes)                                     # HMAC, no el código de 6 dígitos


def test_places_constraints_and_single_cover_per_place(uri):
    base = "insert into places(id, slug, name, description{extra}) values(%s, %s, 'Nombre', 'Descripción de prueba'{vals})"
    assert rejected(uri, "catalog_db", base.format(extra=", difficulty", vals=", 'imposible'"), (uuid.uuid4(), "qa-1")) == "CheckViolation"
    assert rejected(uri, "catalog_db", base.format(extra=", status", vals=", 'borrado'"), (uuid.uuid4(), "qa-2")) == "CheckViolation"
    assert rejected(uri, "catalog_db", base.format(extra=", hike_minutes", vals=", 0"), (uuid.uuid4(), "qa-3")) == "CheckViolation"
    assert rejected(uri, "catalog_db", base.format(extra=", depth_min_m, depth_max_m", vals=", 5, 2"), (uuid.uuid4(), "qa-4")) == "CheckViolation"
    assert rejected(uri, "catalog_db", base.format(extra="", vals=""), (uuid.uuid4(), "cascada-san-miguel")) == "UniqueViolation"
    with connect(uri, "catalog_db") as conn:
        pid = uuid.uuid4()
        conn.execute(base.format(extra="", vals=""), (pid, "qa-cover"))
        photo = "insert into place_photos(id, place_id, alt_text, is_cover, width, height, bytes_full) values(gen_random_uuid(), %s, 'alt', %s, 10, 10, 1)"
        conn.execute(photo, (pid, True))
        conn.execute(photo, (pid, False))
        with pytest.raises(psycopg.errors.UniqueViolation):                                   # una sola portada por lugar
            conn.execute(photo, (pid, True))
        conn.rollback()
