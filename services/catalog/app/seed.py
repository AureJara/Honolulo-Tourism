"""Datos iniciales: las cuatro cascadas del diseño. Idempotente (por ``slug``)."""

from __future__ import annotations

from .extensions import db
from .models import Place

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
