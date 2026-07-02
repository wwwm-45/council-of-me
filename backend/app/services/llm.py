"""
LLM abstraction: generate(prompt, system, temperature, max_tokens) -> str.
Configure via config.LLM_*; use mock when no API key.
"""
import asyncio
import logging
import random
import time
from datetime import UTC, datetime
from typing import AsyncIterator, Optional
from urllib.parse import urlparse, urlunparse

import httpx
from app import config

# Max messages to send per Claude API call to avoid oversized payloads
_MAX_CLAUDE_MESSAGES = 20

# Status codes that are safe to retry (transient server / rate-limit errors)
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Singleton HTTP clients (connection pooling)
# ---------------------------------------------------------------------------

_openai_client = None  # openai.AsyncOpenAI | None
_qwen_client = None    # openai.AsyncOpenAI | None  (DashScope / Bailian)
_deepseek_client = None  # openai.AsyncOpenAI | None  (DeepSeek first-party API)
_httpx_client: Optional[httpx.AsyncClient] = None
_llm_semaphore: Optional[asyncio.Semaphore] = None


def _get_openai_client(model: Optional[str] = None):
    """Lazy-init singleton AsyncOpenAI client for connection reuse."""
    global _openai_client
    if _openai_client is None:
        import openai
        client_kwargs: dict = {"api_key": config.LLM_API_KEY}
        # Always use LLM_BASE_URL for the OpenAI client.
        # Claude models have their own httpx path and never go through this client.
        base_url = _resolve_base_url_for_model(model or config.LLM_MODEL)
        if base_url:
            client_kwargs["base_url"] = base_url
        _openai_client = openai.AsyncOpenAI(**client_kwargs)
    return _openai_client


def _get_qwen_client():
    """Lazy-init singleton AsyncOpenAI client for DashScope / Bailian."""
    global _qwen_client
    if _qwen_client is None:
        if not config.QWEN_BASE_URL:
            raise RuntimeError("QWEN_BASE_URL is not configured")
        import openai
        _qwen_client = openai.AsyncOpenAI(
            api_key=config.QWEN_API_KEY,
            base_url=config.QWEN_BASE_URL,
        )
    return _qwen_client


def _get_deepseek_client():
    """Lazy-init singleton AsyncOpenAI client for DeepSeek's first-party API."""
    global _deepseek_client
    if _deepseek_client is None:
        if not config.DEEPSEEK_BASE_URL:
            raise RuntimeError("DEEPSEEK_BASE_URL is not configured")
        import openai
        _deepseek_client = openai.AsyncOpenAI(
            api_key=config.DEEPSEEK_API_KEY,
            base_url=config.DEEPSEEK_BASE_URL,
        )
    return _deepseek_client


def _get_httpx_client() -> httpx.AsyncClient:
    """Lazy-init singleton httpx client with connection pooling for Claude relay."""
    global _httpx_client
    if _httpx_client is None:
        _httpx_client = httpx.AsyncClient(
            timeout=httpx.Timeout(config.LLM_TIMEOUT_SEC, connect=10.0),
            limits=httpx.Limits(
                max_connections=10,
                max_keepalive_connections=5,
                keepalive_expiry=30,
            ),
        )
    return _httpx_client


def _get_semaphore() -> asyncio.Semaphore:
    """Lazy-init concurrency limiter for LLM calls."""
    global _llm_semaphore
    if _llm_semaphore is None:
        _llm_semaphore = asyncio.Semaphore(config.LLM_MAX_CONCURRENT)
    return _llm_semaphore


async def shutdown_clients() -> None:
    """Close singleton clients. Call from FastAPI lifespan shutdown."""
    global _openai_client, _qwen_client, _deepseek_client, _httpx_client, _llm_semaphore
    if _httpx_client is not None:
        await _httpx_client.aclose()
        _httpx_client = None
    if _openai_client is not None:
        await _openai_client.close()
        _openai_client = None
    if _qwen_client is not None:
        await _qwen_client.close()
        _qwen_client = None
    if _deepseek_client is not None:
        await _deepseek_client.close()
        _deepseek_client = None
    _llm_semaphore = None


def reset_clients() -> None:
    """Invalidate singletons (e.g. after model switch). Next call recreates."""
    global _openai_client, _qwen_client, _deepseek_client, _httpx_client, _llm_semaphore
    # Schedule close of old clients if event loop is running
    old_httpx, old_openai, old_qwen, old_deepseek = _httpx_client, _openai_client, _qwen_client, _deepseek_client
    _httpx_client = None
    _openai_client = None
    _qwen_client = None
    _deepseek_client = None
    _llm_semaphore = None
    try:
        loop = asyncio.get_running_loop()
        if old_httpx:
            loop.create_task(old_httpx.aclose())
        if old_openai:
            loop.create_task(old_openai.close())
        if old_qwen:
            loop.create_task(old_qwen.close())
        if old_deepseek:
            loop.create_task(old_deepseek.close())
    except RuntimeError:
        pass  # No running loop — old clients will be GC'd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_base_url_for_model(model: str) -> Optional[str]:
    """Use model-specific relay base URL when needed."""
    if (model or "").startswith("claude-"):
        return config.CLAUDE_BASE_URL
    if _is_deepseek_model(model):
        return config.DEEPSEEK_BASE_URL
    if _is_qwen_model(model):
        return config.QWEN_BASE_URL
    return _normalize_openai_base_url(config.LLM_BASE_URL)


def _normalize_openai_base_url(base_url: Optional[str]) -> Optional[str]:
    """Append `/v1` for root OpenAI-compatible relay URLs."""
    if not base_url:
        return base_url

    parsed = urlparse(base_url)
    if not parsed.scheme or not parsed.netloc:
        return base_url

    path = parsed.path.rstrip("/")
    if not path:
        path = "/v1"

    return urlunparse(parsed._replace(path=path))


def _is_claude_model(model: str) -> bool:
    return (model or "").startswith("claude-")


def _is_qwen_model(model: str) -> bool:
    m = (model or "").lower()
    return m.startswith("qwen") or m.startswith("qwq")


def _is_deepseek_model(model: str) -> bool:
    return (model or "").lower().startswith("deepseek-")


def _uses_provider_client(model: str) -> bool:
    """Models served by a dedicated OpenAI-compatible client (Qwen on DashScope, DeepSeek official)."""
    return _is_qwen_model(model) or _is_deepseek_model(model)


def _get_provider_client(model: str):
    """Pick the dedicated OpenAI-compatible client for the given provider model."""
    if _is_deepseek_model(model):
        return _get_deepseek_client()
    return _get_qwen_client()


def _provider_credentials_missing(model: str) -> bool:
    """True when the model's dedicated provider lacks an API key or base URL."""
    if _is_deepseek_model(model):
        return not config.DEEPSEEK_API_KEY or not config.DEEPSEEK_BASE_URL
    return not config.QWEN_API_KEY or not config.QWEN_BASE_URL


async def _qwen_chat_stream(
    client,
    model: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    timeout: int,
) -> str:
    """Call DashScope via streaming chat completions."""
    stream = await client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        stream=True,
    )
    chunks: list[str] = []
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            chunks.append(chunk.choices[0].delta.content)
    return "".join(chunks).strip()


def _extract_chat_delta_text(chunk: object) -> str:
    choices = chunk.get("choices") if isinstance(chunk, dict) else getattr(chunk, "choices", None)
    if not isinstance(choices, list) or not choices:
        return ""

    first = choices[0]
    delta = first.get("delta") if isinstance(first, dict) else getattr(first, "delta", None)
    content = delta.get("content") if isinstance(delta, dict) else getattr(delta, "content", None)
    if isinstance(content, str):
        return content
    return ""


async def _qwen_chat_stream_chunks(
    client,
    model: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    timeout: int,
) -> AsyncIterator[str]:
    """Call DashScope via streaming chat completions and yield provider chunks."""
    stream = await client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        stream=True,
    )
    async for chunk in stream:
        text = _extract_chat_delta_text(chunk)
        if text:
            yield text


def _retry_delay(attempt: int, base: float = 1.5) -> float:
    """Exponential backoff with full jitter to avoid thundering herd."""
    max_delay = base * (2 ** attempt)
    return random.uniform(max_delay * 0.5, max_delay)


def is_llm_error(value: str) -> bool:
    """Check if a string is an LLM error sentinel (not valid content)."""
    return isinstance(value, str) and value.startswith("[LLM 调用暂时失败")


def _looks_like_html_document(text: str) -> bool:
    stripped = text.lstrip().lower()
    return stripped.startswith("<!doctype html") or stripped.startswith("<html")


def _raise_if_html_error_page(text: str) -> None:
    if _looks_like_html_document(text):
        raise RuntimeError(
            "LLM endpoint returned HTML instead of API output; check LLM_BASE_URL and wire API settings",
        )


def _sanitize_llm_text(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        return ""
    _raise_if_html_error_page(cleaned)
    return cleaned


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _extract_usage(resp: object) -> tuple[dict | None, int | None, int | None]:
    if isinstance(resp, dict):
        usage = resp.get("usage")
        if isinstance(usage, dict):
            prompt_tokens = usage.get("prompt_tokens") or usage.get("input_tokens")
            completion_tokens = usage.get("completion_tokens") or usage.get("output_tokens")
            return usage, prompt_tokens, completion_tokens

    usage = getattr(resp, "usage", None)
    if usage is None:
        return None, None, None
    if isinstance(usage, dict):
        prompt_tokens = usage.get("prompt_tokens") or usage.get("input_tokens")
        completion_tokens = usage.get("completion_tokens") or usage.get("output_tokens")
        return usage, prompt_tokens, completion_tokens

    prompt_tokens = getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", None)
    completion_tokens = getattr(usage, "completion_tokens", None) or getattr(usage, "output_tokens", None)
    try:
        usage_dict = dict(vars(usage))
    except TypeError:
        usage_dict = None
    return usage_dict, prompt_tokens, completion_tokens


def _stringify_messages(messages: list[dict], system: Optional[str] = None) -> str:
    parts: list[str] = []
    if system:
        parts.append(system)
    parts.extend(
        str(message.get("content", ""))
        for message in messages
        if isinstance(message, dict)
    )
    return "\n".join(parts)


def _emit_eval_record(
    *,
    prompt: str,
    output: str,
    model: str,
    transport: str,
    wire_api: str | None,
    retry_count: int,
    started_at: str,
    started_perf: float,
    usage: dict | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    message_count: int | None = None,
    error_type: str | None = None,
) -> None:
    from eval.harness.observer import LlmCallRecord, emit_llm_record
    from eval.harness.trace_context import get_trace_context

    ctx = get_trace_context()
    if ctx is None:
        return

    emit_llm_record(
        LlmCallRecord(
            run_id=ctx.run_id,
            case_id=ctx.case_id,
            scenario_id=ctx.scenario_id,
            call_site=ctx.call_site,
            stage=ctx.stage,
            is_judge=ctx.is_judge,
            transport=transport,
            model=model,
            provider=config.LLM_PROVIDER,
            wire_api=wire_api,
            prompt_chars=len(prompt),
            output_chars=len(output or ""),
            message_count=message_count,
            retry_count=retry_count,
            latency_ms=int((time.perf_counter() - started_perf) * 1000),
            started_at=started_at,
            finished_at=_utcnow_iso(),
            usage=usage,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=None,
            error_type=error_type,
            replayed=ctx.replay_mode,
        )
    )


# ---------------------------------------------------------------------------
# Main generate functions
# ---------------------------------------------------------------------------

async def generate(
    prompt: str,
    system: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    timeout: Optional[int] = None,
    model: Optional[str] = None,
) -> str:
    """
    Call configured LLM. Returns content string.
    When LLM_API_KEY is not set, returns a mock response for tests.
    """
    effective_model = model or config.LLM_MODEL
    if max_tokens is None:
        max_tokens = config.LLM_MAX_TOKENS_DEFAULT

    # Dedicated-provider models use their own key/base URL (Qwen on DashScope, DeepSeek official).
    if _uses_provider_client(effective_model):
        if _provider_credentials_missing(effective_model):
            logger.warning("Provider API key or base URL not found; using mock response.")
            return _mock_generate(prompt, system)
    elif _is_claude_model(effective_model) and not config.CLAUDE_BASE_URL:
        logger.warning("CLAUDE_BASE_URL not found; using mock response.")
        return _mock_generate(prompt, system)
    elif not config.LLM_API_KEY:
        logger.warning("LLM_API_KEY not found; using mock response.")
        return _mock_generate(prompt, system)

    effective_timeout = timeout or config.LLM_TIMEOUT_SEC
    started_at = _utcnow_iso()
    started_perf = time.perf_counter()

    async with _get_semaphore():
        if config.LLM_PROVIDER == "openai":
            try:
                if _is_claude_model(effective_model):
                    out = await _generate_claude_messages(
                        model=effective_model,
                        prompt=prompt,
                        system=system,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        timeout=effective_timeout,
                    )
                    if out:
                        _emit_eval_record(
                            prompt=prompt,
                            output=out,
                            model=effective_model,
                            transport="claude_messages",
                            wire_api="messages",
                            retry_count=0,
                            started_at=started_at,
                            started_perf=started_perf,
                        )
                        return out

                messages = []
                if system:
                    messages.append({"role": "system", "content": system})
                messages.append({"role": "user", "content": prompt})

                # Dedicated provider — always stream (some models require it)
                if _uses_provider_client(effective_model):
                    client = _get_provider_client(effective_model)
                    for _attempt in range(4):
                        try:
                            text = await _qwen_chat_stream(
                                client, effective_model, messages,
                                temperature, max_tokens, effective_timeout,
                            )
                            if text:
                                _emit_eval_record(
                                    prompt=prompt,
                                    output=text,
                                    model=effective_model,
                                    transport="chat_completions",
                                    wire_api="chat_completions",
                                    retry_count=_attempt,
                                    started_at=started_at,
                                    started_perf=started_perf,
                                )
                                return text
                            break
                        except Exception as e:
                            if _attempt < 3:
                                delay = _retry_delay(_attempt)
                                logger.warning("DashScope API error, retry %d/3 (%.1fs): %s", _attempt + 1, delay, e)
                                await asyncio.sleep(delay)
                                continue
                            raise
                else:
                    client = _get_openai_client(effective_model)
                    for _attempt in range(4):
                        try:
                            if config.LLM_WIRE_API == "responses":
                                resp = await client.responses.create(
                                    model=effective_model,
                                    input=messages,
                                    timeout=effective_timeout,
                                    stream=True,
                                )
                                text = await _extract_responses_stream_text(resp)
                                if text:
                                    usage, tokens_in, tokens_out = _extract_usage(resp)
                                    _emit_eval_record(
                                        prompt=prompt,
                                        output=text,
                                        model=effective_model,
                                        transport="responses",
                                        wire_api=config.LLM_WIRE_API,
                                        retry_count=_attempt,
                                        started_at=started_at,
                                        started_perf=started_perf,
                                        usage=usage,
                                        tokens_in=tokens_in,
                                        tokens_out=tokens_out,
                                    )
                                    return text
                                raise RuntimeError("LLM API returned no text content from Responses API")
                            else:
                                resp = await client.chat.completions.create(
                                    model=effective_model,
                                    messages=messages,
                                    temperature=temperature,
                                    max_tokens=max_tokens,
                                    timeout=effective_timeout,
                                )
                                text = _extract_chat_completion_text(resp)
                                if text:
                                    usage, tokens_in, tokens_out = _extract_usage(resp)
                                    _emit_eval_record(
                                        prompt=prompt,
                                        output=text,
                                        model=effective_model,
                                        transport="chat_completions",
                                        wire_api=config.LLM_WIRE_API,
                                        retry_count=_attempt,
                                        started_at=started_at,
                                        started_perf=started_perf,
                                        usage=usage,
                                        tokens_in=tokens_in,
                                        tokens_out=tokens_out,
                                    )
                                    return text
                                raise RuntimeError("LLM API returned no text content from chat completions")
                        except Exception as e:
                            if _attempt < 3:
                                delay = _retry_delay(_attempt)
                                logger.warning("LLM API error, retry %d/3 (%.1fs): %s", _attempt + 1, delay, e)
                                await asyncio.sleep(delay)
                                continue
                            raise
            except Exception as e:
                logger.error("LLM call failed (key IS set): %s", e)
                return _error_generate(e)

    return _mock_generate(prompt, system)


# ---------------------------------------------------------------------------
# Claude relay with robust retry
# ---------------------------------------------------------------------------

async def _post_claude_with_retry(
    url: str, payload: dict, headers: dict,
    max_retries: int = 4,
    timeout: Optional[int] = None,
) -> dict:
    """POST to Claude relay with retry on transient errors."""
    effective_timeout = timeout or config.LLM_TIMEOUT_SEC
    last_exc: Exception = RuntimeError("No attempts made")
    client = _get_httpx_client()

    for attempt in range(max_retries + 1):
        try:
            resp = await client.post(
                url, json=payload, headers=headers,
                timeout=effective_timeout,
            )
            if resp.status_code not in _RETRYABLE_STATUS_CODES:
                if resp.status_code >= 400:
                    body = resp.text[:500]
                    logger.error("Claude relay %s returned %s: %s", url, resp.status_code, body)
                resp.raise_for_status()
                return resp.json()

            # Retryable status — build error and maybe retry
            last_exc = httpx.HTTPStatusError(
                f"Server error '{resp.status_code}' for url '{url}'",
                request=resp.request,
                response=resp,
            )
            if attempt < max_retries:
                # Respect Retry-After header for 429
                delay = _retry_delay(attempt)
                if resp.status_code == 429:
                    retry_after = resp.headers.get("retry-after")
                    if retry_after:
                        try:
                            delay = min(float(retry_after), 10.0)
                        except ValueError:
                            pass
                logger.warning(
                    "Claude relay %s returned %s, retry %s/%s (%.1fs)",
                    url, resp.status_code, attempt + 1, max_retries, delay,
                )
                await asyncio.sleep(delay)
                continue
            resp.raise_for_status()
        except httpx.HTTPStatusError:
            raise
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                delay = _retry_delay(attempt)
                logger.warning(
                    "Claude relay request error: %s, retry %s/%s (%.1fs)",
                    exc, attempt + 1, max_retries, delay,
                )
                await asyncio.sleep(delay)
            else:
                raise
    raise last_exc


async def _generate_claude_messages(
    *,
    model: str,
    prompt: str,
    system: Optional[str],
    temperature: float,
    max_tokens: int,
    timeout: Optional[int] = None,
) -> str:
    """
    Claude relay path uses Anthropic Messages API style:
    POST {CLAUDE_BASE_URL}/v1/messages
    """
    base_url = _resolve_base_url_for_model(model)
    if not base_url:
        raise RuntimeError("Claude base_url is not configured")

    url = base_url.rstrip("/") + "/v1/messages"
    headers = {
        "Authorization": f"Bearer {config.LLM_API_KEY}",
        "anthropic-version": "2023-06-01",
    }
    payload: dict[str, object] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        payload["system"] = system

    data = await _post_claude_with_retry(url, payload, headers, timeout=timeout)

    content = data.get("content")
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    chunks.append(text)
        return "".join(chunks).strip()
    return ""


def _extract_responses_text(resp: object) -> str:
    """
    Parse text from OpenAI Responses API objects.
    Keep parsing defensive so SDK minor version differences don't break calls.
    """
    if isinstance(resp, str):
        return _sanitize_llm_text(resp)

    if isinstance(resp, dict):
        direct = resp.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return _sanitize_llm_text(direct)

        output = resp.get("output")
        if not isinstance(output, list):
            return ""

        chunks: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for c in content:
                if isinstance(c, dict):
                    text = c.get("text")
                    if isinstance(text, str) and text:
                        chunks.append(text)
        return _sanitize_llm_text("".join(chunks))

    direct = getattr(resp, "output_text", None)
    if isinstance(direct, str) and direct.strip():
        return _sanitize_llm_text(direct)

    output = getattr(resp, "output", None)
    if not isinstance(output, list):
        return ""

    chunks: list[str] = []
    for item in output:
        content = getattr(item, "content", None)
        if not isinstance(content, list):
            continue
        for c in content:
            text = getattr(c, "text", None)
            if isinstance(text, str) and text:
                chunks.append(text)
    return _sanitize_llm_text("".join(chunks))


async def _extract_responses_stream_text(stream_obj: object) -> str:
    """
    Parse text from Responses API stream events.
    Compatible with relays that require stream=true for /responses.
    """
    chunks: list[str] = []

    if not hasattr(stream_obj, "__aiter__"):
        return _extract_responses_text(stream_obj)

    async for event in stream_obj:  # type: ignore[operator]
        event_type = getattr(event, "type", "")

        if event_type == "response.output_text.delta":
            delta = getattr(event, "delta", None)
            if isinstance(delta, str) and delta:
                chunks.append(delta)

        # Some SDK/relay variants put final response in completed event.
        if event_type == "response.completed":
            resp = getattr(event, "response", None)
            text = _extract_responses_text(resp) if resp is not None else ""
            if text:
                return text

    return _sanitize_llm_text("".join(chunks))


async def _extract_responses_stream_chunks(stream_obj: object) -> AsyncIterator[str]:
    """Yield text deltas from Responses API stream events."""
    if not hasattr(stream_obj, "__aiter__"):
        text = _extract_responses_text(stream_obj)
        if text:
            yield text
        return

    yielded = False
    async for event in stream_obj:  # type: ignore[operator]
        event_type = getattr(event, "type", "")
        if event_type == "response.output_text.delta":
            delta = getattr(event, "delta", None)
            if isinstance(delta, str) and delta:
                yielded = True
                yield delta
        elif event_type == "response.completed" and not yielded:
            resp = getattr(event, "response", None)
            text = _extract_responses_text(resp) if resp is not None else ""
            if text:
                yield text


def _coerce_chat_content_text(content: object) -> str:
    """Normalize chat-completion content variants into plain text."""
    if isinstance(content, str):
        return _sanitize_llm_text(content)

    if isinstance(content, dict):
        for key in ("text", "content"):
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                return _sanitize_llm_text(value)
        return ""

    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, str) and item:
                chunks.append(item)
                continue
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text:
                    chunks.append(text)
                    continue
            text = getattr(item, "text", None)
            if isinstance(text, str) and text:
                chunks.append(text)
        return _sanitize_llm_text("".join(chunks))

    text = getattr(content, "text", None)
    if isinstance(text, str) and text.strip():
        return _sanitize_llm_text(text)
    return ""


def _extract_chat_completion_text(resp: object) -> str:
    """Parse text from chat-completion responses across SDK, relay, and dict shapes."""
    if isinstance(resp, str):
        return _sanitize_llm_text(resp)

    choices = resp.get("choices") if isinstance(resp, dict) else getattr(resp, "choices", None)
    if not isinstance(choices, list) or not choices:
        return ""

    first = choices[0]
    if isinstance(first, dict):
        for key in ("message", "delta"):
            text = _coerce_chat_content_text(first.get(key))
            if text:
                return text
        return ""

    for key in ("message", "delta"):
        payload = getattr(first, key, None)
        text = _coerce_chat_content_text(getattr(payload, "content", payload))
        if text:
            return text
    return ""


async def generate_chat(
    messages: list[dict],
    system: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    timeout: Optional[int] = None,
) -> str:
    """
    Multi-turn chat generation. messages: [{"role": "user"|"assistant", "content": "..."}].
    Passes the full conversation history to the LLM for context-aware responses.
    """
    if max_tokens is None:
        max_tokens = config.LLM_MAX_TOKENS_DEFAULT

    # Dedicated-provider models use their own key/base URL (Qwen on DashScope, DeepSeek official).
    if _uses_provider_client(config.LLM_MODEL):
        if _provider_credentials_missing(config.LLM_MODEL):
            logger.warning("Provider API key or base URL not found; using mock response.")
            last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
            return _mock_generate(last_user, system)
    elif _is_claude_model(config.LLM_MODEL) and not config.CLAUDE_BASE_URL:
        logger.warning("CLAUDE_BASE_URL not found; using mock response.")
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        return _mock_generate(last_user, system)
    elif not config.LLM_API_KEY:
        logger.warning("LLM_API_KEY not found; using mock response.")
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        return _mock_generate(last_user, system)

    effective_timeout = timeout or config.LLM_TIMEOUT_SEC
    started_at = _utcnow_iso()
    started_perf = time.perf_counter()
    prompt_text = _stringify_messages(messages, system)

    async with _get_semaphore():
        if config.LLM_PROVIDER == "openai":
            try:
                if _is_claude_model(config.LLM_MODEL):
                    out = await _generate_claude_chat(
                        model=config.LLM_MODEL,
                        messages=messages,
                        system=system,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        timeout=effective_timeout,
                    )
                    if out:
                        _emit_eval_record(
                            prompt=prompt_text,
                            output=out,
                            model=config.LLM_MODEL,
                            transport="claude_messages",
                            wire_api="messages",
                            retry_count=0,
                            started_at=started_at,
                            started_perf=started_perf,
                            message_count=len(messages),
                        )
                        return out

                api_messages = []
                if system:
                    api_messages.append({"role": "system", "content": system})
                api_messages.extend(messages)

                # Dedicated provider — always stream (some models require it)
                if _uses_provider_client(config.LLM_MODEL):
                    client = _get_provider_client(config.LLM_MODEL)
                    for _attempt in range(4):
                        try:
                            text = await _qwen_chat_stream(
                                client, config.LLM_MODEL, api_messages,
                                temperature, max_tokens, effective_timeout,
                            )
                            if text:
                                _emit_eval_record(
                                    prompt=prompt_text,
                                    output=text,
                                    model=config.LLM_MODEL,
                                    transport="chat_completions",
                                    wire_api="chat_completions",
                                    retry_count=_attempt,
                                    started_at=started_at,
                                    started_perf=started_perf,
                                    message_count=len(messages),
                                )
                                return text
                            break
                        except Exception as e:
                            if _attempt < 3:
                                delay = _retry_delay(_attempt)
                                logger.warning("DashScope API error, retry %d/3 (%.1fs): %s", _attempt + 1, delay, e)
                                await asyncio.sleep(delay)
                                continue
                            raise
                else:
                    client = _get_openai_client(config.LLM_MODEL)
                    for _attempt in range(4):
                        try:
                            if config.LLM_WIRE_API == "responses":
                                resp = await client.responses.create(
                                    model=config.LLM_MODEL,
                                    input=api_messages,
                                    timeout=effective_timeout,
                                    stream=True,
                                )
                                text = await _extract_responses_stream_text(resp)
                                if text:
                                    usage, tokens_in, tokens_out = _extract_usage(resp)
                                    _emit_eval_record(
                                        prompt=prompt_text,
                                        output=text,
                                        model=config.LLM_MODEL,
                                        transport="responses",
                                        wire_api=config.LLM_WIRE_API,
                                        retry_count=_attempt,
                                        started_at=started_at,
                                        started_perf=started_perf,
                                        usage=usage,
                                        tokens_in=tokens_in,
                                        tokens_out=tokens_out,
                                        message_count=len(messages),
                                    )
                                    return text
                                raise RuntimeError("LLM API returned no text content from Responses API")
                            else:
                                resp = await client.chat.completions.create(
                                    model=config.LLM_MODEL,
                                    messages=api_messages,
                                    temperature=temperature,
                                    max_tokens=max_tokens,
                                    timeout=effective_timeout,
                                )
                                text = _extract_chat_completion_text(resp)
                                if text:
                                    usage, tokens_in, tokens_out = _extract_usage(resp)
                                    _emit_eval_record(
                                        prompt=prompt_text,
                                        output=text,
                                        model=config.LLM_MODEL,
                                        transport="chat_completions",
                                        wire_api=config.LLM_WIRE_API,
                                        retry_count=_attempt,
                                        started_at=started_at,
                                        started_perf=started_perf,
                                        usage=usage,
                                        tokens_in=tokens_in,
                                        tokens_out=tokens_out,
                                        message_count=len(messages),
                                    )
                                    return text
                                raise RuntimeError("LLM API returned no text content from chat completions")
                        except Exception as e:
                            if _attempt < 3:
                                delay = _retry_delay(_attempt)
                                logger.warning("LLM API error, retry %d/3 (%.1fs): %s", _attempt + 1, delay, e)
                                await asyncio.sleep(delay)
                                continue
                            raise
            except Exception as e:
                logger.error("LLM chat call failed (key IS set): %s", e)
                return _error_generate(e)

    last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    return _mock_generate(last_user, system)


async def generate_chat_stream(
    messages: list[dict],
    system: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    timeout: Optional[int] = None,
) -> AsyncIterator[str]:
    """
    Multi-turn chat streaming. Yields provider text deltas as they arrive.
    Falls back to a single mock/error chunk when streaming cannot be used.
    """
    if max_tokens is None:
        max_tokens = config.LLM_MAX_TOKENS_DEFAULT

    if _uses_provider_client(config.LLM_MODEL):
        if _provider_credentials_missing(config.LLM_MODEL):
            logger.warning("Provider API key or base URL not found; using mock response.")
            last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
            yield _mock_generate(last_user, system)
            return
    elif _is_claude_model(config.LLM_MODEL) and not config.CLAUDE_BASE_URL:
        logger.warning("CLAUDE_BASE_URL not found; using mock response.")
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        yield _mock_generate(last_user, system)
        return
    elif not config.LLM_API_KEY:
        logger.warning("LLM_API_KEY not found; using mock response.")
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        yield _mock_generate(last_user, system)
        return

    effective_timeout = timeout or config.LLM_TIMEOUT_SEC
    started_at = _utcnow_iso()
    started_perf = time.perf_counter()
    prompt_text = _stringify_messages(messages, system)
    chunks: list[str] = []

    async with _get_semaphore():
        if config.LLM_PROVIDER == "openai":
            try:
                if _is_claude_model(config.LLM_MODEL):
                    text = await _generate_claude_chat(
                        model=config.LLM_MODEL,
                        messages=messages,
                        system=system,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        timeout=effective_timeout,
                    )
                    if text:
                        chunks.append(text)
                        yield text
                    _emit_eval_record(
                        prompt=prompt_text,
                        output="".join(chunks),
                        model=config.LLM_MODEL,
                        transport="claude_messages",
                        wire_api="messages",
                        retry_count=0,
                        started_at=started_at,
                        started_perf=started_perf,
                        message_count=len(messages),
                    )
                    return

                api_messages = []
                if system:
                    api_messages.append({"role": "system", "content": system})
                api_messages.extend(messages)

                if _uses_provider_client(config.LLM_MODEL):
                    client = _get_provider_client(config.LLM_MODEL)
                    async for chunk in _qwen_chat_stream_chunks(
                        client,
                        config.LLM_MODEL,
                        api_messages,
                        temperature,
                        max_tokens,
                        effective_timeout,
                    ):
                        chunks.append(chunk)
                        yield chunk
                    _emit_eval_record(
                        prompt=prompt_text,
                        output="".join(chunks),
                        model=config.LLM_MODEL,
                        transport="chat_completions",
                        wire_api="chat_completions",
                        retry_count=0,
                        started_at=started_at,
                        started_perf=started_perf,
                        message_count=len(messages),
                    )
                    return

                client = _get_openai_client(config.LLM_MODEL)
                if config.LLM_WIRE_API == "responses":
                    resp = await client.responses.create(
                        model=config.LLM_MODEL,
                        input=api_messages,
                        timeout=effective_timeout,
                        stream=True,
                    )
                    async for chunk in _extract_responses_stream_chunks(resp):
                        chunks.append(chunk)
                        yield chunk
                    _emit_eval_record(
                        prompt=prompt_text,
                        output="".join(chunks),
                        model=config.LLM_MODEL,
                        transport="responses",
                        wire_api=config.LLM_WIRE_API,
                        retry_count=0,
                        started_at=started_at,
                        started_perf=started_perf,
                        message_count=len(messages),
                    )
                    return

                resp = await client.chat.completions.create(
                    model=config.LLM_MODEL,
                    messages=api_messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=effective_timeout,
                    stream=True,
                )
                async for provider_chunk in resp:
                    chunk = _extract_chat_delta_text(provider_chunk)
                    if not chunk:
                        continue
                    chunks.append(chunk)
                    yield chunk
                _emit_eval_record(
                    prompt=prompt_text,
                    output="".join(chunks),
                    model=config.LLM_MODEL,
                    transport="chat_completions",
                    wire_api=config.LLM_WIRE_API,
                    retry_count=0,
                    started_at=started_at,
                    started_perf=started_perf,
                    message_count=len(messages),
                )
                return
            except Exception as e:
                logger.error("LLM chat stream failed (key IS set): %s", e)
                yield _error_generate(e)
                return

    last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    yield _mock_generate(last_user, system)


async def _generate_claude_chat(
    *,
    model: str,
    messages: list[dict],
    system: Optional[str],
    temperature: float,
    max_tokens: int,
    timeout: Optional[int] = None,
) -> str:
    """Claude relay path for multi-turn chat."""
    base_url = _resolve_base_url_for_model(model)
    if not base_url:
        raise RuntimeError("Claude base_url is not configured")

    # Truncate to last N messages to avoid oversized payloads; keep first user msg for context
    if len(messages) > _MAX_CLAUDE_MESSAGES:
        messages = messages[-_MAX_CLAUDE_MESSAGES:]
        # Claude requires first message to be from user
        if messages and messages[0]["role"] != "user":
            messages = messages[1:]

    url = base_url.rstrip("/") + "/v1/messages"
    headers = {
        "Authorization": f"Bearer {config.LLM_API_KEY}",
        "anthropic-version": "2023-06-01",
    }
    payload: dict[str, object] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": messages,
    }
    if system:
        payload["system"] = system

    data = await _post_claude_with_retry(url, payload, headers, timeout=timeout)

    content = data.get("content")
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    chunks.append(text)
        return "".join(chunks).strip()
    return ""


def _error_generate(exc: Exception) -> str:
    """Fallback response when API key IS configured but the LLM call fails."""
    err_type = type(exc).__name__
    short = str(exc)[:120]
    return f"[LLM 调用暂时失败: {err_type} — {short}] 请稍后重试，或检查后端日志。"


def _mock_generate(prompt: str, system: Optional[str] = None) -> str:
    """Mock response when no API key is configured."""
    if system and "危机" in system:
        return "0.0"
    if "你好" in prompt or not prompt.strip():
        return "你好，我是 Council 的引导助手。"
    return "这是一条模拟回复。请配置 LLM_API_KEY 以使用真实模型。"
