from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator


@dataclass(frozen=True)
class LlmCallRecord:
    run_id: str
    case_id: str | None
    scenario_id: str | None
    call_site: str
    stage: str | None
    is_judge: bool
    transport: str
    model: str
    provider: str
    wire_api: str | None
    prompt_chars: int
    output_chars: int
    message_count: int | None
    retry_count: int
    latency_ms: int
    started_at: str
    finished_at: str
    usage: dict | None
    tokens_in: int | None
    tokens_out: int | None
    cost_usd: float | None
    error_type: str | None
    replayed: bool


_record_sink: Callable[[LlmCallRecord], None] | None = None


def emit_llm_record(record: LlmCallRecord) -> None:
    if _record_sink is not None:
        _record_sink(record)


@contextmanager
def capture_llm_records(records: list[LlmCallRecord]) -> Iterator[list[LlmCallRecord]]:
    global _record_sink
    previous = _record_sink
    _record_sink = records.append
    try:
        yield records
    finally:
        _record_sink = previous
