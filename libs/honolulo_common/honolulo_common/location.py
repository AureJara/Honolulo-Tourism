"""Ubicación única del sistema (RN01) y sus coordenadas configurables (RN02)."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Location:
    code: str
    name: str
    district: str
    province: str
    department: str
    country: str
    latitude: float
    longitude: float
    timezone: str

    @staticmethod
    def from_env() -> "Location":
        # Coordenadas de referencia: 9°17'44"S 75°59'51"O (Tingo María, según el diseño).
        # Deben reemplazarse por las del predio real de Honolulo mediante variables de entorno.
        return Location(
            code=os.getenv("LOCATION_CODE", "honolulo"),
            name=os.getenv("LOCATION_NAME", "Honolulo"),
            district=os.getenv("LOCATION_DISTRICT", "Mariano Dámaso Beraún"),
            province=os.getenv("LOCATION_PROVINCE", "Leoncio Prado"),
            department=os.getenv("LOCATION_DEPARTMENT", "Huánuco"),
            country=os.getenv("LOCATION_COUNTRY", "Perú"),
            latitude=float(os.getenv("LOCATION_LAT", "-9.295556")),
            longitude=float(os.getenv("LOCATION_LON", "-75.9975")),
            timezone=os.getenv("LOCATION_TZ", "America/Lima"),
        )

    def as_dict(self) -> dict:
        return {
            "code": self.code, "name": self.name, "district": self.district,
            "province": self.province, "department": self.department, "country": self.country,
            "latitude": self.latitude, "longitude": self.longitude, "timezone": self.timezone,
        }
