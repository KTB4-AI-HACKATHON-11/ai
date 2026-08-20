from __future__ import annotations

import json
from pathlib import Path

from ai_backend.request_log import (
    COMPACT_TARGET_BYTES,
    MAX_LOG_FILE_BYTES,
    MAX_PAYLOAD_CHARS,
    RequestLogStore,
)


def test_provider_failure_output_is_persisted_and_reloaded(tmp_path: Path) -> None:
    path = tmp_path / "requests.jsonl"
    store = RequestLogStore(str(path))

    store.append(
        "POST",
        "/v1/tasks/generate",
        200,
        9_960,
        "127.0.0.1",
        provider="CEREBRAS",
        fallback_provider="OPENROUTER",
        provider_failure_reason="output_limit",
        provider_finish_reason="length",
        provider_completion_tokens=8_192,
        provider_output='{"tasks":[{"title":"반복 중',
        request_payload='{"message":"오픈 업무"}',
        response_payload='{"tasks":[]}',
        request_photo_preview="data:image/jpeg;base64,preview",
        reference_photo_preview="data:image/jpeg;base64,reference",
    )

    entry = RequestLogStore(str(path)).recent()[0]
    assert entry.providerFailureReason == "output_limit"
    assert entry.providerFinishReason == "length"
    assert entry.providerCompletionTokens == 8_192
    assert entry.providerOutput == '{"tasks":[{"title":"반복 중'
    assert entry.providerOutputTruncated is False
    assert entry.requestPayload == '{"message":"오픈 업무"}'
    assert entry.responsePayload == '{"tasks":[]}'
    assert entry.requestPhotoPreview == "data:image/jpeg;base64,preview"
    assert entry.referencePhotoPreview == "data:image/jpeg;base64,reference"


def test_legacy_request_log_loads_with_empty_provider_output(tmp_path: Path) -> None:
    path = tmp_path / "requests.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": 1,
                "occurredAt": "2026-08-20T00:00:00+00:00",
                "method": "GET",
                "path": "/v1/admin/settings",
                "statusCode": 200,
                "durationMs": 1,
                "clientAddress": "127.0.0.1",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    entry = RequestLogStore(str(path)).recent()[0]
    assert entry.providerOutput == ""
    assert entry.providerCompletionTokens is None
    assert entry.requestPayload == ""
    assert entry.responsePayload == ""
    assert entry.requestPhotoPreview == ""
    assert entry.referencePhotoPreview == ""


def test_request_and_response_payloads_are_bounded(tmp_path: Path) -> None:
    path = tmp_path / "requests.jsonl"
    store = RequestLogStore(str(path))

    store.append(
        "POST",
        "/v1/knowledge/answer",
        200,
        10,
        "127.0.0.1",
        request_payload="요" * (MAX_PAYLOAD_CHARS + 1),
        response_payload="답" * (MAX_PAYLOAD_CHARS + 1),
    )

    entry = store.recent()[0]
    assert len(entry.requestPayload) == MAX_PAYLOAD_CHARS
    assert entry.requestPayloadTruncated is True
    assert len(entry.responsePayload) == MAX_PAYLOAD_CHARS
    assert entry.responsePayloadTruncated is True


def test_large_provider_outputs_compact_below_the_file_limit(tmp_path: Path) -> None:
    path = tmp_path / "requests.jsonl"
    max_file_bytes = 512 * 1024
    compact_target_bytes = 384 * 1024
    store = RequestLogStore(
        str(path),
        max_entries=500,
        max_file_bytes=max_file_bytes,
        compact_target_bytes=compact_target_bytes,
    )

    for index in range(100):
        store.append(
            "POST",
            "/v1/tasks/generate",
            503,
            index,
            "127.0.0.1",
            provider="CEREBRAS",
            provider_failure_reason="invalid_model_output",
            provider_output=str(index) + "가" * 63_999,
        )

    assert path.stat().st_size <= max_file_bytes
    recent = store.recent(limit=500)
    assert recent[0].id == 100
    assert len(recent) < 100


def test_oversized_existing_log_is_loaded_from_bounded_tail_and_repaired(
    tmp_path: Path,
) -> None:
    path = tmp_path / "requests.jsonl"
    lines = []
    for index in range(1, 31):
        lines.append(
            json.dumps(
                {
                    "id": index,
                    "occurredAt": "2026-08-20T00:00:00+00:00",
                    "method": "POST",
                    "path": "/v1/attempts/check",
                    "statusCode": 200,
                    "durationMs": index,
                    "clientAddress": "127.0.0.1",
                    "requestPhotoPreview": "data:image/jpeg;base64," + "a" * 100,
                }
            )
            + "\n"
        )
    path.write_text("".join(lines), encoding="utf-8")
    max_file_bytes = 2_000

    store = RequestLogStore(
        str(path),
        max_file_bytes=max_file_bytes,
        compact_target_bytes=1_500,
    )

    assert store.recent()[0].id == 30
    assert path.stat().st_size <= max_file_bytes


def test_production_log_limits_are_explicit_and_below_one_gibibyte() -> None:
    assert COMPACT_TARGET_BYTES < MAX_LOG_FILE_BYTES
    assert MAX_LOG_FILE_BYTES == 64 * 1024 * 1024
    assert MAX_LOG_FILE_BYTES < 1024 * 1024 * 1024
