"""La web dice la verdad sobre el código de confirmación: solo afirma «te enviamos un correo» si el correo salió."""

from conftest import AUTH, csrf_from, set_pending_email
from test_registration_flow import post_register

REGISTER, RESEND, LOGIN = (f"{AUTH}/api/v1/auth/{p}" for p in ("register", "resend-code", "login"))
LOCAL = {"external": False, "where": "la carpeta .mail"}
SENT = "Te enviamos un código de confirmación por correo."
DEV_NOTICE = "NO se envió a tu correo"


def code_page(client) -> str:
    return client.get("/verificar").get_data(as_text=True)


def test_when_the_mail_stays_in_a_folder_the_page_does_not_claim_it_was_emailed(client, mocked):
    mocked.post(REGISTER, status=201, json={"status": "verification_required", "email": "ana@gmail.com",
                                            "delivery": LOCAL})
    assert post_register(client).status_code == 302
    page = code_page(client)
    assert DEV_NOTICE in page and "la carpeta .mail" in page and "setup-mail" in page     # el aviso con el remedio
    assert "no se envió</strong> a" in page                                                 # y el texto fijo también
    assert SENT not in page and "Enviamos un código de 6 dígitos" not in page


def test_when_the_mail_really_leaves_the_page_says_it_was_sent(client, mocked):
    mocked.post(REGISTER, status=201, json={"status": "verification_required", "email": "ana@gmail.com",
                                            "delivery": {"external": True}})
    post_register(client)
    page = code_page(client)
    assert SENT in page and "Enviamos un código de 6 dígitos" in page
    assert DEV_NOTICE not in page and "setup-mail" not in page


def test_an_auth_service_that_reports_nothing_keeps_the_usual_message(client, mocked):
    """Compatibilidad: sin el dato se trata como un envío normal."""
    mocked.post(REGISTER, status=201, json={"status": "verification_required", "email": "ana@gmail.com"})
    post_register(client)
    page = code_page(client)
    assert SENT in page and DEV_NOTICE not in page


def test_the_development_notice_is_escaped(client, mocked):
    mocked.post(REGISTER, status=201, json={"status": "verification_required", "email": "ana@gmail.com",
                                            "delivery": {"external": False, "where": "<script>alert(1)</script>"}})
    post_register(client)
    page = code_page(client)
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_resending_also_tells_the_truth(client, mocked):
    mocked.post(RESEND, status=202, json={"status": "accepted", "delivery": LOCAL})
    set_pending_email(client)
    resp = client.post("/verificar/reenviar", data={"csrf_token": csrf_from(client, "/verificar")},
                       follow_redirects=True)
    page = resp.get_data(as_text=True)
    assert DEV_NOTICE in page and "te enviamos un código nuevo" not in page


def test_logging_in_with_an_unconfirmed_email_tells_the_truth_too(client, mocked):
    mocked.post(LOGIN, status=403, json={"code": "EMAIL_NOT_VERIFIED", "email": "ana@example.com",
                                         "detail": "Confirma tu correo.", "delivery": LOCAL})
    resp = client.post("/ingresar", data={"email": "ana@example.com", "password": "clave1234",
                                          "csrf_token": csrf_from(client, "/ingresar")}, follow_redirects=True)
    page = resp.get_data(as_text=True)
    assert resp.request.path == "/verificar" and DEV_NOTICE in page and "Te enviamos un código nuevo" not in page


def test_a_later_real_delivery_clears_the_development_notice(client, mocked):
    """Si se configura el correo entre dos intentos, la pantalla del código deja de decir que no se envió."""
    set_pending_email(client)
    mocked.post(RESEND, status=202, json={"status": "accepted", "delivery": LOCAL})
    client.post("/verificar/reenviar", data={"csrf_token": csrf_from(client, "/verificar")})
    assert "no se envió</strong> a" in code_page(client)
    mocked.replace("POST", RESEND, status=202, json={"status": "accepted", "delivery": {"external": True}})
    client.post("/verificar/reenviar", data={"csrf_token": csrf_from(client, "/verificar")})
    assert "Enviamos un código de 6 dígitos" in code_page(client)
