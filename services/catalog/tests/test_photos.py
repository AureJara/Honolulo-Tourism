"""Escenario 6: el administrador puede modificar las fotos. Incluye las pruebas de abuso en subidas."""

import io
import struct
import zlib

import pytest
from PIL import Image

from app import storage as storage_mod
from conftest import first_place_id, make_image, upload

ADMIN = "/api/v1/admin/places"


def files_on_disk(app):
    return sorted(p.name for p in app.extensions["storage"].root.glob("*"))


@pytest.fixture()
def place_id(seeded, admin):
    return first_place_id(seeded, admin)


# -------------------------------------------------------------- subida válida
def test_first_photo_becomes_the_cover_and_is_served_as_webp(app, seeded, admin, place_id):
    resp = upload(seeded, admin, place_id, make_image("JPEG"), alt="Catarata con pozas esmeralda")
    assert resp.status_code == 201
    photo = resp.get_json()
    assert photo["is_cover"] is True and photo["alt"] == "Catarata con pozas esmeralda"
    assert photo["url"].endswith("-1600.webp") and photo["thumb_url"].endswith("-640.webp")

    media = seeded.get(photo["url"])
    assert media.status_code == 200 and media.mimetype == "image/webp"
    assert "immutable" in media.headers["Cache-Control"] and media.headers["X-Content-Type-Options"] == "nosniff"
    assert Image.open(io.BytesIO(media.data)).format == "WEBP"

    place = seeded.get("/api/v1/places").get_json()["items"][0]
    assert place["cover"]["url"] == photo["url"] and place["cover"]["alt"] == "Catarata con pozas esmeralda"
    assert len(place["photos"]) == 1


def test_images_are_resized_and_thumbnailed_without_upscaling(seeded, admin, place_id):
    big = upload(seeded, admin, place_id, make_image("PNG", size=(3000, 2000))).get_json()
    full = Image.open(io.BytesIO(seeded.get(big["url"]).data))
    thumb = Image.open(io.BytesIO(seeded.get(big["thumb_url"]).data))
    assert full.size == (1600, 1067) and thumb.size == (640, 427)
    # QA D4: el ancho/alto guardado y declarado es el de la imagen ALMACENADA, no el del original subido
    assert (big["width"], big["height"], big["thumb_width"]) == (1600, 1067, 640)
    small = upload(seeded, admin, place_id, make_image("JPEG", size=(500, 400))).get_json()
    assert Image.open(io.BytesIO(seeded.get(small["url"]).data)).size == (500, 400)
    assert (small["width"], small["height"], small["thumb_width"]) == (500, 400, 500)


def test_portrait_thumbnail_width_follows_the_longest_side(seeded, admin, place_id):
    photo = upload(seeded, admin, place_id, make_image("PNG", size=(1200, 2400))).get_json()
    thumb = Image.open(io.BytesIO(seeded.get(photo["thumb_url"]).data))
    assert (photo["width"], photo["height"]) == (800, 1600) and abs(photo["thumb_width"] - thumb.size[0]) <= 1
    cover = seeded.get("/api/v1/places").get_json()["items"][0]["cover"]
    assert (cover["width"], cover["thumb_width"]) == (800, photo["thumb_width"])


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_accepted_formats(seeded, admin, place_id, fmt):
    assert upload(seeded, admin, place_id, make_image(fmt)).status_code == 201


def test_exif_metadata_including_gps_is_stripped(seeded, admin, place_id):
    img = Image.new("RGB", (800, 600), (10, 100, 60))
    exif = Image.Exif()
    exif[0x010F] = "CamaraSecreta"                      # Make
    exif[0x8825] = {1: "S", 2: (9.0, 17.0, 44.0)}       # GPSInfo
    out = io.BytesIO()
    img.save(out, format="JPEG", exif=exif)
    assert b"CamaraSecreta" in out.getvalue()
    photo = upload(seeded, admin, place_id, out.getvalue()).get_json()
    served = seeded.get(photo["url"]).data
    assert b"CamaraSecreta" not in served and not Image.open(io.BytesIO(served)).getexif()


def test_exif_orientation_is_applied_before_stripping(seeded, admin, place_id):
    img = Image.new("RGB", (800, 400), (200, 30, 30))
    exif = Image.Exif()
    exif[0x0112] = 6                                     # girar 90°
    out = io.BytesIO()
    img.save(out, format="JPEG", exif=exif)
    photo = upload(seeded, admin, place_id, out.getvalue()).get_json()
    assert Image.open(io.BytesIO(seeded.get(photo["url"]).data)).size == (400, 800)


def test_client_filename_is_ignored_so_path_traversal_is_impossible(app, seeded, admin, place_id):
    resp = upload(seeded, admin, place_id, make_image(), filename="../../../etc/passwd.jpg")
    assert resp.status_code == 201
    names = files_on_disk(app)
    assert len(names) == 2 and all(storage_mod.KEY_RE.match(n) for n in names)


# ------------------------------------------------------ portada y orden
def test_cover_switching_keeps_exactly_one_cover(seeded, admin, place_id):
    a = upload(seeded, admin, place_id, make_image(), alt="Foto A de la cascada").get_json()
    b = upload(seeded, admin, place_id, make_image(), alt="Foto B de la cascada").get_json()
    assert a["is_cover"] is True and b["is_cover"] is False
    assert seeded.patch(f"/api/v1/admin/photos/{b['id']}", headers=admin, json={"is_cover": True}).status_code == 200
    photos = seeded.get(f"{ADMIN}/{place_id}", headers=admin).get_json()["photos"]
    assert {p["id"]: p["is_cover"] for p in photos} == {a["id"]: False, b["id"]: True}
    c = upload(seeded, admin, place_id, make_image(), alt="Foto C de la cascada", is_cover="true").get_json()
    covers = [p["id"] for p in seeded.get(f"{ADMIN}/{place_id}", headers=admin).get_json()["photos"] if p["is_cover"]]
    assert covers == [c["id"]]


def test_current_cover_cannot_be_unset_without_choosing_another(seeded, admin, place_id):
    a = upload(seeded, admin, place_id, make_image()).get_json()
    resp = seeded.patch(f"/api/v1/admin/photos/{a['id']}", headers=admin, json={"is_cover": False})
    assert resp.status_code == 422


def test_alt_text_can_be_edited_and_is_audited(seeded, admin, place_id):
    a = upload(seeded, admin, place_id, make_image(), alt="Texto alternativo inicial").get_json()
    resp = seeded.patch(f"/api/v1/admin/photos/{a['id']}", headers=admin, json={"alt_text": "Texto alternativo nuevo"})
    assert resp.get_json()["alt"] == "Texto alternativo nuevo"
    actions = [h["action"] for h in seeded.get(f"{ADMIN}/{place_id}/history", headers=admin).get_json()["items"]]
    assert actions == ["photo_updated", "photo_added"]
    assert seeded.patch(f"/api/v1/admin/photos/{a['id']}", headers=admin, json={"alt_text": "x"}).status_code == 422


def test_deleting_the_cover_promotes_the_next_photo_and_removes_files(app, seeded, admin, place_id):
    a = upload(seeded, admin, place_id, make_image(), alt="Primera foto cubierta").get_json()
    b = upload(seeded, admin, place_id, make_image(), alt="Segunda foto cubierta").get_json()
    assert len(files_on_disk(app)) == 4
    assert seeded.delete(f"/api/v1/admin/photos/{a['id']}", headers=admin).status_code == 204
    assert len(files_on_disk(app)) == 2 and seeded.get(a["url"]).status_code == 404
    photos = seeded.get(f"{ADMIN}/{place_id}", headers=admin).get_json()["photos"]
    assert [(p["id"], p["is_cover"]) for p in photos] == [(b["id"], True)]
    seeded.delete(f"/api/v1/admin/photos/{b['id']}", headers=admin)
    assert seeded.get("/api/v1/places").get_json()["items"][0]["cover"] is None and files_on_disk(app) == []
    assert seeded.delete(f"/api/v1/admin/photos/{b['id']}", headers=admin).status_code == 404


def test_photo_limit_per_place(seeded, admin, place_id):
    for i in range(4):
        assert upload(seeded, admin, place_id, make_image(), alt=f"Foto número {i}").status_code == 201
    resp = upload(seeded, admin, place_id, make_image(), alt="Una más de la cuenta")
    assert resp.status_code == 409 and resp.get_json()["code"] == "PHOTO_LIMIT"


# --------------------------------------------------- subidas maliciosas o inválidas
def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def pixel_bomb_png(width, height) -> bytes:
    """PNG válido de width×height que comprime a pocos KB (bomba de descompresión)."""
    raw = zlib.compress((b"\x00" + b"\x00" * (width * 3)) * height, 9)
    return (b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + _png_chunk(b"IDAT", raw) + _png_chunk(b"IEND", b""))


@pytest.mark.parametrize("name,data", [
    ("texto", b"esto no es una imagen, aunque se llame foto.jpg"),
    ("vacio", b""),
    ("php", b"<?php system($_GET['c']); ?>"),
    ("html", b"<html><script>alert(1)</script></html>"),
    ("svg", b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><script>alert(1)</script></svg>'),
    ("exe", b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 200),
    ("jpeg-truncado", make_image("JPEG", size=(2000, 1500))[:900]),
    ("jpeg-corrupto", make_image("JPEG")[:200] + b"\xff" * 400),
])
def test_non_images_are_rejected_and_nothing_is_stored(app, seeded, admin, place_id, name, data):
    resp = upload(seeded, admin, place_id, data)
    assert resp.status_code == 422 and resp.get_json()["code"] == "INVALID_IMAGE", name
    assert files_on_disk(app) == []
    assert seeded.get(f"{ADMIN}/{place_id}", headers=admin).get_json()["photos"] == []


def test_gif_is_not_an_accepted_format(seeded, admin, place_id):
    resp = upload(seeded, admin, place_id, make_image("GIF"), filename="animada.gif")
    assert resp.status_code == 422 and "Formato no admitido" in resp.get_json()["detail"]


def test_animated_webp_is_rejected(seeded, admin, place_id):
    frames = [Image.new("RGB", (400, 400), c) for c in ((255, 0, 0), (0, 255, 0))]
    out = io.BytesIO()
    frames[0].save(out, format="WEBP", save_all=True, append_images=frames[1:], duration=100)
    assert upload(seeded, admin, place_id, out.getvalue()).status_code == 422


def test_image_with_a_polyglot_payload_is_cleaned_by_reencoding(seeded, admin, place_id):
    data = make_image("JPEG") + b"<script>alert('xss')</script><?php evil(); ?>"
    photo = upload(seeded, admin, place_id, data).get_json()
    served = seeded.get(photo["url"]).data
    assert b"<script>" not in served and b"<?php" not in served


def test_too_small_image_is_rejected(seeded, admin, place_id):
    resp = upload(seeded, admin, place_id, make_image(size=(120, 90)))
    assert resp.status_code == 422 and "muy pequeña" in resp.get_json()["detail"]


def test_decompression_bomb_is_rejected_without_decoding_it(app, seeded, admin, place_id):
    bomb = pixel_bomb_png(9000, 9000)              # 81 MP: pesa muy poco pero ocuparía ~240 MB
    assert len(bomb) < 300 * 1024
    resp = upload(seeded, admin, place_id, bomb)
    assert resp.status_code == 413 and resp.get_json()["code"] == "FILE_TOO_LARGE"
    assert files_on_disk(app) == []


def test_oversized_file_is_rejected(app, seeded, admin, place_id):
    noisy = io.BytesIO()
    Image.effect_noise((1200, 1200), 80).convert("RGB").save(noisy, format="PNG")      # ruido: no comprime
    assert len(noisy.getvalue()) > 400 * 1024
    resp = upload(seeded, admin, place_id, noisy.getvalue())
    assert resp.status_code == 413
    assert files_on_disk(app) == []


def test_file_between_the_two_limits_is_rejected_by_the_image_check(app, seeded, admin, place_id):
    noisy = io.BytesIO()
    Image.effect_noise((360, 360), 80).convert("RGB").save(noisy, format="PNG")
    size = len(noisy.getvalue())
    assert 300 * 1024 < size < 400 * 1024, size
    resp = upload(seeded, admin, place_id, noisy.getvalue())
    assert resp.status_code == 413 and resp.get_json()["code"] == "FILE_TOO_LARGE"


@pytest.mark.parametrize("alt", ["", "  ", "ab", "x" * 201])
def test_alt_text_is_required_for_accessibility(seeded, admin, place_id, alt):
    resp = upload(seeded, admin, place_id, make_image(), alt=alt)
    assert resp.status_code == 422 and "alt_text" in [e["field"] for e in resp.get_json()["errors"]]


def test_missing_file_field_is_422(seeded, admin, place_id):
    resp = seeded.post(f"{ADMIN}/{place_id}/photos", headers=admin, data={"alt_text": "Foto sin archivo"},
                       content_type="multipart/form-data")
    assert resp.status_code == 422 and "file" in [e["field"] for e in resp.get_json()["errors"]]


def test_normal_user_cannot_upload(seeded, user, place_id):
    resp = seeded.post(f"{ADMIN}/{place_id}/photos", headers=user,
                       data={"file": (io.BytesIO(make_image()), "a.jpg"), "alt_text": "Intento de un usuario"},
                       content_type="multipart/form-data")
    assert resp.status_code == 403


def test_failed_storage_write_leaves_no_orphans_and_no_row(app, seeded, admin, place_id, monkeypatch):
    real = app.extensions["storage"].write
    calls = {"n": 0}

    def flaky(key, data):
        calls["n"] += 1
        if calls["n"] == 2:                          # falla al guardar la miniatura
            raise OSError("disco lleno")
        real(key, data)

    monkeypatch.setattr(app.extensions["storage"], "write", flaky)
    resp = upload(seeded, admin, place_id, make_image())
    assert resp.status_code == 500
    monkeypatch.undo()
    assert files_on_disk(app) == []
    assert seeded.get(f"{ADMIN}/{place_id}", headers=admin).get_json()["photos"] == []


# ------------------------------------------------------------- servir archivos
@pytest.mark.parametrize("key", [
    "..%2f..%2fetc%2fpasswd", "....//....//x", "a.webp", f"{'g' * 32}-1600.webp", f"{'a' * 32}-9999.webp",
    f"{'a' * 32}-1600.jpg", f"{'a' * 32}-1600.webp%00.png", "..", ".", f"{'a' * 31}-1600.webp",
])
def test_media_route_only_serves_well_formed_keys(seeded, key):
    assert seeded.get(f"/media/{key}").status_code == 404


def test_media_supports_conditional_requests(seeded, admin, place_id):
    photo = upload(seeded, admin, place_id, make_image()).get_json()
    first = seeded.get(photo["url"])
    second = seeded.get(photo["url"], headers={"If-None-Match": first.headers["ETag"]})
    assert second.status_code == 304
