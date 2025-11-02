"""Tests for :mod:`egaicheck.ocr`."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

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
def _mock_response(text: str) -> Mock:
    response = Mock()
    response.raise_for_status = Mock()
    response.text = text
    return response


class DecodeMarkMimeTypeTests(TestCase):
    def test_decode_mark_uses_detected_mime_type(self) -> None:
        cases = [
            ("image.png", "image/png"),
            ("image.custom", "image/jpeg"),
        ]

        for filename, expected_mime in cases:
            with self.subTest(filename=filename):
                with TemporaryDirectory() as tmpdir:
                    image_path = Path(tmpdir) / filename
                    image_path.write_bytes(b"binary-data")

                    mock_response = _mock_response("<pre>decoded\nvalue</pre>")

                    with patch("egaicheck.ocr.requests.post", return_value=mock_response) as mock_post:
                        decoded = decode_mark_from_image(image_path)

                    self.assertEqual(decoded, "decoded")
                    self.assertEqual(mock_post.call_count, 1)

                    call_args = mock_post.call_args.kwargs
                    files_payload = call_args["files"]
                    self.assertEqual(files_payload["f"][2], expected_mime)
