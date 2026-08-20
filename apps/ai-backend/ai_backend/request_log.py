from __future__ import annotations

import json
import os
import threading
from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

MAX_LOG_ENTRIES = 500
MAX_LOG_FILE_BYTES = 64 * 1024 * 1024
COMPACT_TARGET_BYTES = 56 * 1024 * 1024
MAX_PROVIDER_OUTPUT_CHARS = 64_000
MAX_PAYLOAD_CHARS = 128_000
MAX_PHOTO_PREVIEW_CHARS = 280_000


@dataclass(frozen=True)
class RequestLogEntry:
    id: int
    occurredAt: str
    method: str
    path: str
    statusCode: int
    durationMs: int
    clientAddress: str = ""
    provider: str = ""
    model: str = ""
    cacheStatus: str = ""
    fallbackProvider: str = ""
    outcome: str = ""
    errorCode: str = ""
    taskCount: int | None = None
    providerFailureReason: str = ""
    providerFinishReason: str = ""
    providerCompletionTokens: int | None = None
    providerOutput: str = ""
    providerOutputTruncated: bool = False
    requestPayload: str = ""
    requestPayloadTruncated: bool = False
    responsePayload: str = ""
    responsePayloadTruncated: bool = False
    requestPhotoPreview: str = ""
    referencePhotoPreview: str = ""


class RequestLogStore:
    def __init__(
        self,
        path: str | None,
        max_entries: int = MAX_LOG_ENTRIES,
        *,
        max_file_bytes: int = MAX_LOG_FILE_BYTES,
        compact_target_bytes: int = COMPACT_TARGET_BYTES,
    ) -> None:
        self._path = Path(path) if path else None
        self._max_entries = max(1, min(max_entries, MAX_LOG_ENTRIES))
        self._max_file_bytes = max(1, min(max_file_bytes, MAX_LOG_FILE_BYTES))
        self._compact_target_bytes = max(
            1,
            min(compact_target_bytes, self._max_file_bytes),
        )
        self._entries: deque[RequestLogEntry] = deque(maxlen=self._max_entries)
        self._lock = threading.RLock()
        self._next_id = 1
        self._load()

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            file_size = self._path.stat().st_size
            with self._path.open("rb") as stream:
                start = max(0, file_size - self._max_file_bytes)
                if start:
                    stream.seek(start)
                    stream.readline(self._max_file_bytes)
                raw = stream.read(self._max_file_bytes)
            lines = raw.decode("utf-8", errors="ignore").splitlines()
        except OSError:
            return
        for line in lines[-self._max_entries :]:
            try:
                entry = RequestLogEntry(**json.loads(line))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            self._entries.append(entry)
            self._next_id = max(self._next_id, entry.id + 1)
        if file_size > self._max_file_bytes:
            self._compact()

    def append(
        self,
        method: str,
        path: str,
        status_code: int,
        duration_ms: int,
        client_address: str,
        *,
        provider: str = "",
        model: str = "",
        cache_status: str = "",
        fallback_provider: str = "",
        outcome: str = "",
        error_code: str = "",
        task_count: int | None = None,
        provider_failure_reason: str = "",
        provider_finish_reason: str = "",
        provider_completion_tokens: int | None = None,
        provider_output: str = "",
        provider_output_truncated: bool = False,
        request_payload: str = "",
        request_payload_truncated: bool = False,
        response_payload: str = "",
        response_payload_truncated: bool = False,
        request_photo_preview: str = "",
        reference_photo_preview: str = "",
    ) -> None:
        with self._lock:
            entry = RequestLogEntry(
                id=self._next_id,
                occurredAt=datetime.now(UTC).isoformat(),
                method=method,
                path=path,
                statusCode=status_code,
                durationMs=max(0, duration_ms),
                clientAddress=client_address[:255],
                provider=provider[:32],
                model=model[:160],
                cacheStatus=cache_status[:16],
                fallbackProvider=fallback_provider[:32],
                outcome=outcome[:32],
                errorCode=error_code[:64],
                taskCount=task_count,
                providerFailureReason=provider_failure_reason[:64],
                providerFinishReason=provider_finish_reason[:32],
                providerCompletionTokens=provider_completion_tokens,
                providerOutput=provider_output[:MAX_PROVIDER_OUTPUT_CHARS],
                providerOutputTruncated=(
                    provider_output_truncated
                    or len(provider_output) > MAX_PROVIDER_OUTPUT_CHARS
                ),
                requestPayload=request_payload[:MAX_PAYLOAD_CHARS],
                requestPayloadTruncated=(
                    request_payload_truncated
                    or len(request_payload) > MAX_PAYLOAD_CHARS
                ),
                responsePayload=response_payload[:MAX_PAYLOAD_CHARS],
                responsePayloadTruncated=(
                    response_payload_truncated
                    or len(response_payload) > MAX_PAYLOAD_CHARS
                ),
                requestPhotoPreview=(
                    request_photo_preview
                    if len(request_photo_preview) <= MAX_PHOTO_PREVIEW_CHARS
                    else ""
                ),
                referencePhotoPreview=(
                    reference_photo_preview
                    if len(reference_photo_preview) <= MAX_PHOTO_PREVIEW_CHARS
                    else ""
                ),
            )
            self._next_id += 1
            self._entries.append(entry)
            self._persist(entry)

    def recent(self, limit: int = 100) -> list[RequestLogEntry]:
        with self._lock:
            return list(reversed(self._entries))[
                : max(1, min(limit, self._max_entries))
            ]

    def _persist(self, entry: RequestLogEntry) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(asdict(entry), ensure_ascii=False) + "\n"
            line_size = len(line.encode("utf-8"))
            current_size = self._path.stat().st_size if self._path.exists() else 0
            if current_size + line_size > self._max_file_bytes:
                self._compact()
                return
            descriptor = os.open(
                self._path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                0o600,
            )
            with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
                stream.write(line)
            os.chmod(self._path, 0o600)
        except OSError:
            return

    def _compact(self) -> None:
        if self._path is None:
            return
        retained: list[RequestLogEntry] = []
        retained_lines: list[str] = []
        retained_size = 0
        for entry in reversed(self._entries):
            line = json.dumps(asdict(entry), ensure_ascii=False) + "\n"
            line_size = len(line.encode("utf-8"))
            if line_size > self._max_file_bytes:
                continue
            if retained and retained_size + line_size > self._compact_target_bytes:
                break
            retained.append(entry)
            retained_lines.append(line)
            retained_size += line_size
        retained.reverse()
        retained_lines.reverse()
        temporary = self._path.with_suffix(f"{self._path.suffix}.tmp")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.writelines(retained_lines)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
            os.chmod(self._path, 0o600)
            self._entries = deque(retained, maxlen=self._max_entries)
        finally:
            if temporary.exists():
                temporary.unlink()
