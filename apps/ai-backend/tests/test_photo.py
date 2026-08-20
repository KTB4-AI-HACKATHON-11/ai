from __future__ import annotations

import asyncio
import base64
import hashlib
from io import BytesIO

import httpx
import pytest
from PIL import Image

from ai_backend.photo import (
    PhotoUnavailableError,
    load_verified_photo,
    normalize_cerebras_photo,
    photo_log_preview,
    verify_photo_bytes,
)
from ai_backend.schemas import PhotoInput

JPEG = b"\xff\xd8\xff\xd9"


async def public_resolver(_host: str, _port: int) -> set[str]:
    return {"93.184.216.34"}


def photo(**overrides: object) -> PhotoInput:
    values = {
        "mimeType": "image/jpeg",
        "sizeBytes": len(JPEG),
        "sha256": hashlib.sha256(JPEG).hexdigest(),
        "url": "https://storage.example.com/photo.jpg",
        **overrides,
    }
    return PhotoInput.model_validate(values)


def test_verified_photo_becomes_data_url() -> None:
    assert verify_photo_bytes(photo(), JPEG) == "data:image/jpeg;base64,/9j/2Q=="


@pytest.mark.parametrize(
    ("metadata", "data"),
    [
        ({"sizeBytes": len(JPEG) + 1}, JPEG),
        ({"sha256": "0" * 64}, JPEG),
        ({"mimeType": "image/png"}, JPEG),
    ],
)
def test_photo_metadata_must_match_downloaded_bytes(
    metadata: dict[str, object], data: bytes
) -> None:
    with pytest.raises(PhotoUnavailableError):
        verify_photo_bytes(photo(**metadata), data)


def test_photo_download_is_verified_without_following_redirects() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://storage.example.com/photo.jpg"
        return httpx.Response(200, content=JPEG)

    result = asyncio.run(
        load_verified_photo(
            photo(),
            transport=httpx.MockTransport(handler),
            resolver=public_resolver,
        )
    )
    assert result == "data:image/jpeg;base64,/9j/2Q=="


def test_photo_download_rejects_redirect() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://other.example.com"})

    with pytest.raises(PhotoUnavailableError):
        asyncio.run(
            load_verified_photo(
                photo(),
                transport=httpx.MockTransport(handler),
                resolver=public_resolver,
            )
        )


def test_photo_download_rejects_private_literal_address() -> None:
    private = photo(url="https://127.0.0.1/photo.jpg")

    with pytest.raises(PhotoUnavailableError):
        asyncio.run(load_verified_photo(private))


def test_photo_download_rejects_hostname_resolving_to_private_address() -> None:
    async def private_resolver(_host: str, _port: int) -> set[str]:
        return {"10.0.0.8"}

    with pytest.raises(PhotoUnavailableError):
        asyncio.run(load_verified_photo(photo(), resolver=private_resolver))


def test_cerebras_webp_is_converted_to_jpeg() -> None:
    source = BytesIO()
    Image.new("RGB", (8, 8), "white").save(source, format="WEBP")
    data_url = "data:image/webp;base64," + base64.b64encode(source.getvalue()).decode(
        "ascii"
    )

    result = normalize_cerebras_photo(data_url)

    assert result.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(result.split(",", 1)[1]).startswith(b"\xff\xd8\xff")


def test_photo_log_preview_is_a_bounded_jpeg() -> None:
    source = BytesIO()
    Image.new("RGB", (1_200, 800), "navy").save(source, format="PNG")
    data_url = "data:image/png;base64," + base64.b64encode(source.getvalue()).decode(
        "ascii"
    )

    result = photo_log_preview(data_url)

    assert result.startswith("data:image/jpeg;base64,")
    rendered = base64.b64decode(result.split(",", 1)[1])
    with Image.open(BytesIO(rendered)) as preview:
        assert max(preview.size) <= 640


def test_photo_log_preview_rejects_invalid_data() -> None:
    assert photo_log_preview("data:image/jpeg;base64,not-base64") == ""
