"""HTTP client for interacting with the check1.fsrar.ru service."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

LOGGER = logging.getLogger(__name__)

BASE_URL = "https://check1.fsrar.ru/"
DEFAULT_TIMEOUT = 25


@dataclass
class PendingCheck:
    """Data required to submit a mark verification request."""

    mark_code: str
    action_url: str
    mark_field: str
    captcha_field: str
    extra_fields: Dict[str, str] = field(default_factory=dict)
    headers: Dict[str, str] = field(default_factory=dict)


class Check1FsrarClient:
    """Minimal client that emulates the logic of the official checker."""

    def __init__(self, timeout: int = DEFAULT_TIMEOUT) -> None:
        self._session = requests.Session()
        self.timeout = timeout

    def prepare_check(self, mark_code: str) -> Tuple[PendingCheck, bytes]:
        """Retrieve the check form and captcha for a mark.

        Args:
            mark_code: Decoded value of the DataMatrix mark.

        Returns:
            A tuple containing the :class:`PendingCheck` metadata and the raw
            captcha bytes that have to be shown to the Telegram user.
        """

        LOGGER.info("Fetching FS RAR check form")
        response = self._session.get(BASE_URL, timeout=self.timeout)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        form = soup.find("form")
        if not form:
            raise RuntimeError("Не удалось найти форму проверки на сайте ФСРАР.")

        action = form.get("action") or ""
        action_url = urljoin(BASE_URL, action)

        hidden_fields: Dict[str, str] = {}
        mark_field = None
        captcha_field = None

        for input_el in form.find_all("input"):
            name = input_el.get("name")
            if not name:
                continue
            value = input_el.get("value", "")
            input_type = (input_el.get("type") or "").lower()
            hidden_fields[name] = value

            name_lower = name.lower()
            if input_type in {"text", "search", "tel", ""}:
                if any(key in name_lower for key in ("mark", "kod", "code", "datamatrix", "shtrih", "sdata")):
                    mark_field = name
            if "captcha" in name_lower or name_lower.endswith("_captcha"):
                captcha_field = name

        if not mark_field:
            raise RuntimeError("Не удалось определить поле для ввода кода марки.")
        if not captcha_field:
            captcha_field = "captcha"

        # Attempt to find anti-forgery tokens and preserve them in headers.
        headers: Dict[str, str] = {}
        token_value = hidden_fields.get("__RequestVerificationToken")
        if token_value:
            headers["X-Request-Verification-Token"] = token_value

        # Remove the mark/captcha placeholders because they will be filled later.
        hidden_fields.pop(mark_field, None)
        hidden_fields.pop(captcha_field, None)

        captcha_img = form.find("img")
        if not captcha_img:
            captcha_img = soup.find("img", src=lambda src: src and "captcha" in src.lower())
        if not captcha_img:
            raise RuntimeError("Не удалось найти изображение с капчей.")

        captcha_src = captcha_img.get("src")
        captcha_url = urljoin(BASE_URL, captcha_src)
        captcha_response = self._session.get(captcha_url, timeout=self.timeout)
        captcha_response.raise_for_status()

        pending = PendingCheck(
            mark_code=mark_code,
            action_url=action_url,
            mark_field=mark_field,
            captcha_field=captcha_field,
            extra_fields=hidden_fields,
            headers=headers,
        )
        return pending, captcha_response.content

    def submit_check(self, pending: PendingCheck, captcha_value: str):
        """Submit the mark verification form.

        Args:
            pending: Metadata obtained via :meth:`prepare_check`.
            captcha_value: Captcha solution provided by the Telegram user.

        Returns:
            Raw response text from the FS RAR service.
        """

        form_data = dict(pending.extra_fields)
        form_data[pending.mark_field] = pending.mark_code
        form_data[pending.captcha_field] = captcha_value

        headers = {"Referer": BASE_URL, "Content-Type": "application/x-www-form-urlencoded"}
        headers.update(pending.headers)

        LOGGER.info("Submitting mark check request")
        response = self._session.post(
            pending.action_url,
            data=form_data,
            headers=headers,
            timeout=self.timeout,
        )
        response.raise_for_status()

        if "application/json" in response.headers.get("Content-Type", ""):
            return response.json()
        return response.text

