from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from typing import Iterator


@dataclass(frozen=True)
class EvalTraceContext:
    run_id: str
    case_id: str | None
    scenario_id: str | None
    call_site: str
    stage: str | None
    is_judge: bool
    replay_mode: bool


_TRACE_CONTEXT: ContextVar[EvalTraceContext | None] = ContextVar(
    "eval_trace_context",
    default=None,
)


def get_trace_context() -> EvalTraceContext | None:
    return _TRACE_CONTEXT.get()


@contextmanager
def bind_eval_trace(context: EvalTraceContext) -> Iterator[EvalTraceContext]:
    token: Token = _TRACE_CONTEXT.set(context)
    try:
        yield context
    finally:
        _TRACE_CONTEXT.reset(token)


@contextmanager
def push_trace_stage(stage: str) -> Iterator[EvalTraceContext | None]:
    current = get_trace_context()
    if current is None:
        yield None
        return

    token = _TRACE_CONTEXT.set(replace(current, stage=stage))
    try:
        yield get_trace_context()
    finally:
        _TRACE_CONTEXT.reset(token)
