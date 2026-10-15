"""Datos iniciales: las cuatro cascadas del diseño. Idempotente (por ``slug``)."""

from __future__ import annotations

from pathlib import Path

from flask import current_app

from .extensions import db
from .images import process_image
from .models import Place, PlacePhoto

SEED_PLACES = [
    {"slug": "catarata-velo-de-las-ninfas", "name": "Catarata Velo de las Ninfas", "difficulty": "moderate",
     "hike_minutes": 45, "depth_label": "Piscina Natural", "depth_min_m": None, "depth_max_m": 3.5, "sort_order": 1,
     "description": "Famosa por sus toboganes de piedra natural pulida por el cauce y sus cristalinas lagunas "
                    "esmeralda. El epicentro ideal para canyoning guiado y refrescantes baños naturales."},
    {"slug": "cascada-san-miguel", "name": "Cascada San Miguel", "difficulty": "easy",
     "hike_minutes": 25, "depth_label": "Pozas", "depth_min_m": 1.8, "depth_max_m": 2.5, "sort_order": 2,
     "description": "Ruta de trekking rodeada de selva viva y acantilados de musgo. Sus remansos pacíficos y "
                    "amplias pozas naturales la convierten en el paraje perfecto para nadar con total tranquilidad."},
    {"slug": "catarata-santa-carmen", "name": "Catarata Santa Carmen", "difficulty": "moderate",
     "hike_minutes": 35, "depth_label": "Profundidad", "depth_min_m": None, "depth_max_m": 4.0, "sort_order": 3,
     "description": "Potente torrente que alimenta pozas esmeraldas en un cañón bañado por el sol. Ideal para el "
                    "avistamiento de aves amazónicas y relajarse en sus aguas revitalizantes."},
    {"slug": "catarata-gloriapata", "name": "Catarata Gloriapata", "difficulty": "moderate",
     "hike_minutes": 50, "depth_label": "Profundidad", "depth_min_m": None, "depth_max_m": 3.0, "sort_order": 4,
     "description": "Ruta mística iniciada con un puente colgante sobre el río Monzón. Rodeada de exuberante flora "
                    "silvestre, helechos gigantes y orquídeas que resguardan un manantial puro."},
]


def seed_places() -> int:
    created = 0
    for data in SEED_PLACES:
        if db.session.execute(db.select(Place.id).where(Place.slug == data["slug"])).first():
            continue
        db.session.add(Place(status="published", **data))
        created += 1
    db.session.commit()
    return created


# Fotos que se cargan con los datos iniciales: slug -> (archivo en ``seed_media/``, texto alternativo).
SEED_PHOTOS = {
    "catarata-velo-de-las-ninfas": (
        "velo-de-las-ninfas.jpg",
        "Catarata Velo de las Ninfas: caída de agua blanca entre rocas cubiertas de musgo y vegetación "
        "selvática, que termina en una poza de aguas verdes."),
    "cascada-san-miguel": (
        "cascada-san-miguel.jpg",
        "Cascada San Miguel: salto de agua sobre una pared de roca con musgo, rodeado de selva densa, que baja "
        "en rápidos hacia el río."),
    "catarata-santa-carmen": (
        "catarata-santa-carmen.jpg",
        "Catarata Santa Carmen: torrente blanco entre paredes de roca con musgo que cae a una poza de aguas "
        "esmeralda, con una gran roca clara en primer plano."),
    "catarata-gloriapata": (
        "catarata-gloriapata.jpg",
        "Catarata Gloriapata: cascada delgada que cae entre rocas cubiertas de musgo y helechos hacia una poza "
        "de aguas turquesa."),
}
SEED_MEDIA = Path(__file__).resolve().parent / "seed_media"


def seed_photos() -> int:
    """Asigna la foto inicial a los lugares sembrados que aún no tienen ninguna. Idempotente.

    No toca los lugares con fotos: lo que el administrador haya subido o cambiado no se sobrescribe.
    """
    added = 0
    storage = current_app.extensions["storage"]
    for slug, (filename, alt_text) in SEED_PHOTOS.items():
        place = db.session.execute(db.select(Place).where(Place.slug == slug)).scalar_one_or_none()
        if place is None or place.photos:
            continue
        processed = process_image((SEED_MEDIA / filename).read_bytes(), 10 * 1024 * 1024)
        photo = PlacePhoto(place_id=place.id, alt_text=alt_text, is_cover=True, sort_order=0,
                           width=processed.width, height=processed.height, bytes_full=len(processed.full),
                           uploaded_by=None)
        db.session.add(photo)
        db.session.flush()
        storage.write(photo.key_full, processed.full)
        storage.write(photo.key_thumb, processed.thumb)
        db.session.commit()
        added += 1
    return added
