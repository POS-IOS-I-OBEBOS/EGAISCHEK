"""Tests for :mod:`egaicheck.ocr`."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from egaicheck.ocr import decode_mark_from_image


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
