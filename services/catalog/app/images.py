"""Validación y reprocesado de fotos subidas.

Nunca se confía en la extensión ni en el ``Content-Type``: el archivo se decodifica con Pillow, se
comprueba su formato real y se **vuelve a codificar** a WebP. Así se descartan metadatos (EXIF/GPS),
contenido adicional («polyglots») y cualquier carga activa embebida.
"""

from __future__ import annotations

import io
import warnings
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_PIXELS = 40_000_000          # ~40 MP: por encima se rechaza antes de decodificar (bombas de descompresión)
MIN_SIDE = 300
FULL_SIZE, THUMB_SIZE = 1600, 640

Image.MAX_IMAGE_PIXELS = MAX_PIXELS * 2          # Pillow lanza DecompressionBombError al doble de este valor


class ImageRejected(ValueError):
    def __init__(self, message: str, *, too_large: bool = False) -> None:
        super().__init__(message)
        self.too_large = too_large


@dataclass
class ProcessedImage:
    full: bytes
    thumb: bytes
    width: int          # dimensiones de la imagen COMPLETA ya almacenada (no las del original subido)
    height: int
    thumb_width: int
    thumb_height: int


def _encode(img: Image.Image, size: int, quality: int) -> tuple[bytes, tuple[int, int]]:
    copy = img.copy()
    copy.thumbnail((size, size), Image.LANCZOS)       # no amplía imágenes pequeñas
    out = io.BytesIO()
    copy.save(out, format="WEBP", quality=quality, method=4)
    return out.getvalue(), copy.size


def process_image(data: bytes, max_bytes: int) -> ProcessedImage:
    if not data:
        raise ImageRejected("El archivo está vacío.")
    if len(data) > max_bytes:
        raise ImageRejected(f"La foto supera el máximo de {max_bytes // (1024 * 1024)} MB.", too_large=True)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            probe = Image.open(io.BytesIO(data))
            fmt, (w, h) = probe.format, probe.size
            if fmt not in ALLOWED_FORMATS:
                raise ImageRejected("Formato no admitido. Usa JPEG, PNG o WebP.")
            if w * h > MAX_PIXELS:
                raise ImageRejected("La imagen tiene demasiados píxeles.", too_large=True)
            if getattr(probe, "n_frames", 1) > 1:
                raise ImageRejected("No se admiten imágenes animadas.")
            probe.verify()                              # detecta archivos truncados o corruptos
            img = Image.open(io.BytesIO(data))          # verify() invalida el objeto: se reabre
            img = ImageOps.exif_transpose(img)
            has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
            img = img.convert("RGBA" if has_alpha else "RGB")
    except ImageRejected:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageRejected("La imagen tiene demasiados píxeles.", too_large=True) from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise ImageRejected("El archivo no es una imagen válida.") from exc

    if min(img.size) < MIN_SIDE:
        raise ImageRejected(f"La imagen es muy pequeña: debe medir al menos {MIN_SIDE} px por lado.")
    full, full_size = _encode(img, FULL_SIZE, 82)
    thumb, thumb_size = _encode(img, THUMB_SIZE, 78)
    return ProcessedImage(full, thumb, *full_size, *thumb_size)
