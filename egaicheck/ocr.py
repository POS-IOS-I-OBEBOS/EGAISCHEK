"""Helpers for decoding EGAIS marks from images."""
from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

import requests
from bs4 import BeautifulSoup

LOGGER = logging.getLogger(__name__)

ZXING_ENDPOINT = "https://zxing.org/w/decode"


def decode_mark_from_image(image_path: Path, timeout: int = 30) -> str:
    """Decode a DataMatrix mark from an image.

    The implementation relies on the public ZXing web decoder because the
    official EGAIS check expects a textual representation of the mark. The
    service returns an HTML document where the decoded payload is embedded
    into a ``<pre>`` tag.  The function extracts the text and returns the
    first non-empty line.

    Args:
        image_path: Path to the image file provided by the Telegram user.
        timeout: Number of seconds to wait for the remote service.

    Returns:
        The decoded mark as a string.

    Raises:
        RuntimeError: If the mark cannot be decoded.
    """

    LOGGER.info("Decoding mark from %s via ZXing service", image_path)
    mime_type, _ = mimetypes.guess_type(image_path.name)
    if mime_type is None:
        mime_type = "image/jpeg"

    with Path(image_path).open("rb") as image_file:
        files = {"f": (image_path.name, image_file, mime_type)}
        data = {"full": "true"}
        response = requests.post(ZXING_ENDPOINT, files=files, data=data, timeout=timeout)

    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise RuntimeError("Не удалось декодировать марку через сервис ZXing.") from exc

    soup = BeautifulSoup(response.text, "html.parser")
    pre_block = soup.find("pre")
    if not pre_block:
        raise RuntimeError("Сервис ZXing не вернул текст с расшифровкой марки.")

    text = pre_block.get_text("\n").strip()
    for line in text.splitlines():
        cleaned = line.strip()
        if cleaned:
            LOGGER.debug("Decoded mark: %s", cleaned)
            return cleaned

    raise RuntimeError("Не удалось найти расшифрованный код марки в ответе ZXing.")

