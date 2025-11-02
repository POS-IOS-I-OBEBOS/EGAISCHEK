"""Helpers for decoding EGAIS marks from images."""
from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from PIL import Image
from pylibdmtx.pylibdmtx import decode

LOGGER = logging.getLogger(__name__)


def _decode_payload(data: bytes | None) -> str:
    """Decode bytes returned by :func:`pylibdmtx.pylibdmtx.decode`.

    The ZXing service previously returned UTF-8 text.  We preserve this
    behaviour by attempting to decode using UTF-8 first and falling back to
    Windows-1251 which is commonly used in Russian EGAIS codes.
    """

    if not data:
        return ""

    for encoding in ("utf-8", "cp1251", "latin-1"):
        try:
            return data.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    # ``latin-1`` never raises ``UnicodeDecodeError`` so this line is mostly
    # defensive, but it keeps ``mypy`` happy and signals an empty result when
    # nothing sensible could be decoded.
    return ""


def decode_mark_from_image(image_path: Path, timeout: int = 30) -> str:
    """Decode a DataMatrix mark from an image using :mod:`pylibdmtx`.

    Args:
        image_path: Path to the image file provided by the Telegram user.
        timeout: Kept for backward compatibility; ignored because decoding is
            performed locally.

    Returns:
        The decoded mark as a string.

    Raises:
        RuntimeError: If the mark cannot be decoded.
    """

    del timeout  # ``timeout`` is ignored but preserved for API compatibility.

    LOGGER.info("Decoding mark from %s via pylibdmtx", image_path)
    LOGGER.info("Decoding mark from %s via ZXing service", image_path)
    mime_type, _ = mimetypes.guess_type(image_path.name)
    if mime_type is None:
        mime_type = "image/jpeg"

    with Path(image_path).open("rb") as image_file:
        files = {"f": (image_path.name, image_file, mime_type)}
        data = {"full": "true"}
        response = requests.post(ZXING_ENDPOINT, files=files, data=data, timeout=timeout)

    try:
        with Image.open(Path(image_path)) as image:
            decoded_symbols = decode(image)
    except (FileNotFoundError, OSError) as exc:  # pragma: no cover - pass-through errors
        raise RuntimeError("Не удалось открыть изображение для декодирования DataMatrix.") from exc

    if not decoded_symbols:
        raise RuntimeError("Не удалось распознать DataMatrix код на изображении.")

    for symbol in decoded_symbols:
        payload = _decode_payload(symbol.data)
        if payload:
            LOGGER.debug("Decoded mark: %s", payload)
            return payload

    raise RuntimeError("Не удалось получить содержимое DataMatrix кода.")

