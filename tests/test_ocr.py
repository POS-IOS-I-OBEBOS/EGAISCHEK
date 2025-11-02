"""Tests for :mod:`egaicheck.ocr`."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from egaicheck.ocr import decode_mark_from_image


class DecodeMarkFromImageTests(TestCase):
    def test_decode_returns_first_non_empty_payload(self) -> None:
        symbol = SimpleNamespace(data="decoded\nvalue".encode("utf-8"))

        with patch("egaicheck.ocr.Image.open") as mock_open, patch(
            "egaicheck.ocr.decode", return_value=[symbol]
        ) as mock_decode:
            mock_image = MagicMock()
            mock_open.return_value.__enter__.return_value = mock_image

            result = decode_mark_from_image(Path("/tmp/image.png"))

        self.assertEqual(result, "decoded\nvalue")
        mock_open.assert_called_once()
        mock_decode.assert_called_once_with(mock_image)

    def test_decode_raises_when_no_symbols_found(self) -> None:
        with patch("egaicheck.ocr.Image.open") as mock_open, patch(
            "egaicheck.ocr.decode", return_value=[]
        ):
            mock_open.return_value.__enter__.return_value = MagicMock()

            with self.assertRaisesRegex(RuntimeError, "Не удалось распознать DataMatrix"):
                decode_mark_from_image(Path("/tmp/missing.png"))

    def test_decode_ignores_empty_payloads(self) -> None:
        empty = SimpleNamespace(data=b"")
        valid = SimpleNamespace(data=" 12345\n".encode("cp1251"))

        with patch("egaicheck.ocr.Image.open") as mock_open, patch(
            "egaicheck.ocr.decode", return_value=[empty, valid]
        ):
            mock_open.return_value.__enter__.return_value = MagicMock()

            result = decode_mark_from_image(Path("/tmp/image.png"))

        self.assertEqual(result, "12345")
