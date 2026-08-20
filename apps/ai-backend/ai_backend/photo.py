from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import ipaddress
import socket
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from io import BytesIO

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.datastructures import UploadFile

from .schemas import PhotoInput

MAX_PHOTO_BYTES = 10 * 1024 * 1024
MODEL_PHOTO_MAX_BYTES = 1024 * 1024
MODEL_PHOTO_TARGET_BYTES = 600 * 1024
MODEL_PHOTO_MAX_DIMENSION = 1280
MODEL_PHOTO_DIMENSIONS = (1280, 1120, 960, 800)
MODEL_PHOTO_QUALITIES = (82, 72, 62)
LOG_PREVIEW_MAX_BYTES = 200_000
LOG_PREVIEW_MAX_DIMENSION = 640
REFERENCE_PHOTO_CACHE_MAX_ENTRIES = 64
REFERENCE_PHOTO_CACHE_MAX_BYTES = 64 * 1024 * 1024
HostResolver = Callable[[str, int], Awaitable[set[str]]]
PhotoValueLoader = Callable[[], Awaitable[str]]


class PhotoUnavailableError(Exception):
    def __init__(self, field: str | None = None) -> None:
        super().__init__()
        self.field = field


async def _resolve_host(host: str, port: int) -> set[str]:
    def resolve() -> set[str]:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        return {str(record[4][0]) for record in records}

    try:
        return await asyncio.to_thread(resolve)
    except OSError as error:
        raise PhotoUnavailableError from error


def _public_address(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).is_global
    except ValueError:
        return False


async def _verify_public_photo_host(
    photo: PhotoInput, resolver: HostResolver | None = None
) -> None:
    host = photo.url.host
    if not host:
        raise PhotoUnavailableError
    literal = host.removeprefix("[").removesuffix("]")
    try:
        address = ipaddress.ip_address(literal)
    except ValueError:
        addresses = await (resolver or _resolve_host)(host, photo.url.port or 443)
        if not addresses or any(not _public_address(value) for value in addresses):
            raise PhotoUnavailableError
    else:
        if not address.is_global:
            raise PhotoUnavailableError


def _detected_mime(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def verify_photo_bytes(photo: PhotoInput, data: bytes) -> str:
    if len(data) != photo.sizeBytes or len(data) > MAX_PHOTO_BYTES:
        raise PhotoUnavailableError
    digest = hashlib.sha256(data).hexdigest()
    if not hmac.compare_digest(digest, photo.sha256):
        raise PhotoUnavailableError
    return verified_photo_data_url(photo.mimeType, data)


def verified_photo_data_url(mime_type: str, data: bytes) -> str:
    if not data or len(data) > MAX_PHOTO_BYTES:
        raise PhotoUnavailableError
    if _detected_mime(data) != mime_type:
        raise PhotoUnavailableError
    try:
        with Image.open(BytesIO(data)) as source:
            source.load()
            oriented = ImageOps.exif_transpose(source)
            if oriented.mode in {"RGBA", "LA"} or (
                oriented.mode == "P" and "transparency" in oriented.info
            ):
                rgba = oriented.convert("RGBA")
                normalized = Image.new("RGB", rgba.size, "white")
                normalized.paste(rgba, mask=rgba.getchannel("A"))
            else:
                normalized = oriented.convert("RGB")

            best = b""
            for dimension in MODEL_PHOTO_DIMENSIONS:
                candidate = normalized.copy()
                candidate.thumbnail(
                    (dimension, dimension),
                    Image.Resampling.LANCZOS,
                )
                for quality in MODEL_PHOTO_QUALITIES:
                    output = BytesIO()
                    candidate.save(
                        output,
                        format="JPEG",
                        quality=quality,
                        optimize=True,
                    )
                    rendered = output.getvalue()
                    if not best or len(rendered) < len(best):
                        best = rendered
                    if len(rendered) <= MODEL_PHOTO_TARGET_BYTES:
                        encoded = base64.b64encode(rendered).decode("ascii")
                        return f"data:image/jpeg;base64,{encoded}"
    except (
        OSError,
        ValueError,
        Image.DecompressionBombError,
        UnidentifiedImageError,
    ) as error:
        raise PhotoUnavailableError from error

    if not best or len(best) > MODEL_PHOTO_MAX_BYTES:
        raise PhotoUnavailableError
    encoded = base64.b64encode(best).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


async def _download_photo(
    client: httpx.AsyncClient,
    photo: PhotoInput,
) -> bytes:
    async with client.stream("GET", str(photo.url)) as response:
        if (
            response.is_redirect
            or response.status_code < 200
            or response.status_code >= 300
        ):
            raise PhotoUnavailableError
        declared_length = int(response.headers.get("content-length", "0"))
        if declared_length > MAX_PHOTO_BYTES:
            raise PhotoUnavailableError
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_PHOTO_BYTES:
                raise PhotoUnavailableError
    return bytes(body)


async def load_verified_photo(
    photo: PhotoInput,
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: HostResolver | None = None,
    client: httpx.AsyncClient | None = None,
) -> str:
    try:
        await _verify_public_photo_host(photo, resolver)
        if client is None:
            async with httpx.AsyncClient(
                timeout=20,
                follow_redirects=False,
                transport=transport,
            ) as owned_client:
                body = await _download_photo(owned_client, photo)
        else:
            if transport is not None:
                raise ValueError("client와 transport는 함께 지정할 수 없습니다.")
            body = await _download_photo(client, photo)
    except PhotoUnavailableError:
        raise
    except (httpx.HTTPError, ValueError) as error:
        raise PhotoUnavailableError from error
    return verify_photo_bytes(photo, body)


class SharedPhotoDownloader:
    """요청 사이에 HTTP 연결 풀을 재사용하는 검증 사진 로더."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=20, follow_redirects=False)

    async def __call__(self, photo: PhotoInput) -> str:
        return await load_verified_photo(photo, client=self._client)

    async def aclose(self) -> None:
        await self._client.aclose()


class ReferencePhotoCache:
    """검증 완료된 모범 사진 변환본만 SHA-256 기준으로 보관한다."""

    def __init__(
        self,
        max_entries: int = REFERENCE_PHOTO_CACHE_MAX_ENTRIES,
        max_bytes: int = REFERENCE_PHOTO_CACHE_MAX_BYTES,
    ) -> None:
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._entries: OrderedDict[str, str] = OrderedDict()
        self._total_bytes = 0
        self._lock = asyncio.Lock()

    @staticmethod
    def _key(photo: PhotoInput) -> str:
        return f"{photo.sha256}:{photo.mimeType}:{photo.sizeBytes}"

    async def get_or_load(
        self,
        photo: PhotoInput,
        loader: PhotoValueLoader,
    ) -> str:
        key = self._key(photo)
        async with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)
                return cached

        value = await loader()
        value_bytes = len(value.encode("ascii"))
        if value_bytes > self._max_bytes:
            return value

        async with self._lock:
            existing = self._entries.get(key)
            if existing is not None:
                self._entries.move_to_end(key)
                return existing
            self._entries[key] = value
            self._total_bytes += value_bytes
            while (
                len(self._entries) > self._max_entries
                or self._total_bytes > self._max_bytes
            ):
                _, removed = self._entries.popitem(last=False)
                self._total_bytes -= len(removed.encode("ascii"))
        return value


async def load_uploaded_photo(photo: UploadFile) -> str:
    mime_type = photo.content_type or ""
    try:
        data = await photo.read(MAX_PHOTO_BYTES + 1)
    except OSError as error:
        raise PhotoUnavailableError from error
    finally:
        await photo.close()
    return verified_photo_data_url(mime_type, data)


def normalize_cerebras_photo(data_url: str) -> str:
    """Cerebras가 받지 않는 WebP만 검증된 JPEG로 변환한다."""

    prefix = "data:image/webp;base64,"
    if not data_url.startswith(prefix):
        return data_url
    try:
        raw = base64.b64decode(data_url[len(prefix) :], validate=True)
        with Image.open(BytesIO(raw)) as source:
            source.load()
            converted = source.convert("RGB")
            output = BytesIO()
            converted.save(output, format="JPEG", quality=90, optimize=True)
        encoded = output.getvalue()
    except (OSError, ValueError, UnidentifiedImageError) as error:
        raise PhotoUnavailableError from error
    if not encoded or len(encoded) > MAX_PHOTO_BYTES:
        raise PhotoUnavailableError
    return f"data:image/jpeg;base64,{base64.b64encode(encoded).decode('ascii')}"


def photo_log_preview(data_url: str) -> str:
    """검증 사진을 요청 기록용 작은 JPEG 미리보기로 변환한다."""

    try:
        metadata, encoded = data_url.split(",", 1)
        if metadata not in {
            "data:image/jpeg;base64",
            "data:image/png;base64",
            "data:image/webp;base64",
        }:
            return ""
        raw = base64.b64decode(encoded, validate=True)
        if not raw or len(raw) > MAX_PHOTO_BYTES:
            return ""
        with Image.open(BytesIO(raw)) as source:
            source.load()
            oriented = ImageOps.exif_transpose(source)
            if oriented.mode in {"RGBA", "LA"} or (
                oriented.mode == "P" and "transparency" in oriented.info
            ):
                rgba = oriented.convert("RGBA")
                preview = Image.new("RGB", rgba.size, "white")
                preview.paste(rgba, mask=rgba.getchannel("A"))
            else:
                preview = oriented.convert("RGB")
            preview.thumbnail(
                (LOG_PREVIEW_MAX_DIMENSION, LOG_PREVIEW_MAX_DIMENSION),
                Image.Resampling.LANCZOS,
            )
            rendered = b""
            for quality in (72, 58, 45):
                output = BytesIO()
                preview.save(output, format="JPEG", quality=quality, optimize=True)
                rendered = output.getvalue()
                if len(rendered) <= LOG_PREVIEW_MAX_BYTES:
                    break
    except (
        OSError,
        ValueError,
        binascii.Error,
        Image.DecompressionBombError,
        UnidentifiedImageError,
    ):
        return ""
    if not rendered or len(rendered) > LOG_PREVIEW_MAX_BYTES:
        return ""
    return f"data:image/jpeg;base64,{base64.b64encode(rendered).decode('ascii')}"
