"""Test doubles for OpenAI SDK at the client boundary (async stream + sync non-stream).

``EduAgent`` uses ``await self._async_client.chat.completions.create(..., stream=True)``
for ``run_turn_stream``; summarizer uses sync ``self._client.chat.completions.create``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Chunk helpers (OpenAI streaming chat completion shape)
# ---------------------------------------------------------------------------


def stream_chunk(
    *,
    delta_content: str | None = None,
    delta_tool_calls: list[Any] | None = None,
    choice_finish_reason: str | None = None,
    usage_tokens: tuple[int, int] | None = None,
) -> Any:
    delta = SimpleNamespace(content=delta_content, tool_calls=delta_tool_calls)
    choice = SimpleNamespace(delta=delta, finish_reason=choice_finish_reason)
    ch = SimpleNamespace(choices=[choice])
    if usage_tokens is not None:
        ch.usage = SimpleNamespace(
            prompt_tokens=usage_tokens[0],
            completion_tokens=usage_tokens[1],
        )
    return ch


async def async_iter_text_response(
    text: str | None,
    *,
    finish_reason: str = "stop",
    usage: tuple[int, int] = (80, 2),
) -> AsyncIterator[Any]:
    """Yield streaming chunks whose concatenated delta.content equals *text*."""
    if text:
        yield stream_chunk(delta_content=text, choice_finish_reason=None)
    yield stream_chunk(
        delta_content=None,
        choice_finish_reason=finish_reason,
        usage_tokens=usage,
    )


async def async_iter_tool_calls_response(
    tool_index: int,
    tool_id: str,
    name: str,
    arguments_json: str,
    *,
    finish_reason: str = "tool_calls",
    usage: tuple[int, int] = (50, 1),
) -> AsyncIterator[Any]:
    """Single chunk with full tool call in delta (valid for agent accumulator)."""
    fn = SimpleNamespace(name=name, arguments=arguments_json)
    tc_delta = SimpleNamespace(index=tool_index, id=tool_id, function=fn)
    yield stream_chunk(
        delta_content=None,
        delta_tool_calls=[tc_delta],
        choice_finish_reason=finish_reason,
        usage_tokens=usage,
    )


def make_sync_chat_response(
    choices: list[Any],
    *,
    prompt_tokens: int = 80,
    completion_tokens: int = 2,
) -> Any:
    resp = MagicMock()
    resp.choices = choices
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    resp.usage = usage
    return resp


def make_sync_choice(
    content: str | None = None,
    tool_calls: list[Any] | None = None,
    finish_reason: str = "stop",
) -> Any:
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    msg.model_dump.return_value = {
        "role": "assistant",
        "content": content,
        "tool_calls": tool_calls,
    }
    choice = MagicMock()
    choice.finish_reason = finish_reason
    choice.message = msg
    return choice


def attach_offline_openai_clients(
    agent: Any,
    *,
    stream_factory: Any | None = None,
    sync_response: Any | None = None,
) -> None:
    """Replace agent LLM clients with fakes. *stream_factory* is ``async def (*a,**k) -> async iterable``."""
    if sync_response is None:
        sync_response = make_sync_chat_response(
            [make_sync_choice(content="[test summary]", finish_reason="stop")],
            prompt_tokens=10,
            completion_tokens=2,
        )

    if stream_factory is None:

        async def _default(*_a: Any, **_k: Any) -> Any:
            async def _gen() -> AsyncIterator[Any]:
                async for c in async_iter_text_response("ok"):
                    yield c

            return _gen()

        stream_factory = _default

    async_client = MagicMock()
    chat = MagicMock()
    completions = MagicMock()
    completions.create = AsyncMock(side_effect=stream_factory)
    chat.completions = completions
    async_client.chat = chat

    sync_client = MagicMock()
    sync_client.chat = MagicMock()
    sync_client.chat.completions = MagicMock()
    sync_client.chat.completions.create = MagicMock(return_value=sync_response)

    agent._async_client = async_client
    agent._client = sync_client


def stream_factory_fixed_text(content: str) -> Any:
    """One LLM round: assistant text is *content* (streaming)."""

    async def _factory(*_a: Any, **_k: Any) -> Any:
        async def _gen() -> AsyncIterator[Any]:
            async for c in async_iter_text_response(content):
                yield c

        return _gen()

    return _factory


def stream_factory_tool_then_text(
    *,
    tool_index: int = 0,
    tool_id: str = "call_1",
    tool_name: str,
    tool_arguments_json: str,
    assistant_after_tool: str,
) -> Any:
    """First ``create`` → tool_calls stream; second → plain assistant *assistant_after_tool*."""
    phase = [0]

    async def _factory(*_a: Any, **_k: Any) -> Any:
        phase[0] += 1
        if phase[0] == 1:

            async def _g1() -> AsyncIterator[Any]:
                async for c in async_iter_tool_calls_response(
                    tool_index, tool_id, tool_name, tool_arguments_json
                ):
                    yield c

            return _g1()

        async def _g2() -> AsyncIterator[Any]:
            async for c in async_iter_text_response(assistant_after_tool):
                yield c

        return _g2()

    return _factory


def stream_factory_repeated_tool(
    tool_name: str,
    tool_arguments_json: str = "{}",
) -> Any:
    """Every ``create`` returns the same tool_calls-only stream (for iteration budget tests)."""
    n = [0]

    async def _factory(*_a: Any, **_k: Any) -> Any:
        n[0] += 1

        async def _g() -> AsyncIterator[Any]:
            async for c in async_iter_tool_calls_response(
                0,
                f"tc{n[0]}",
                tool_name,
                tool_arguments_json,
            ):
                yield c

        return _g()

    return _factory


def sequenced_text_streams(texts: list[str]) -> Any:
    """Return ``stream_factory`` suitable for ``attach_offline_openai_clients`` — one stream per call."""
    idx = [0]

    async def _factory(*_a: Any, **_k: Any) -> Any:
        i = idx[0]
        idx[0] += 1
        t = texts[i] if i < len(texts) else texts[-1]

        async def _gen() -> AsyncIterator[Any]:
            async for c in async_iter_text_response(t):
                yield c

        return _gen()

    return _factory


@contextmanager
def patch_agent_openai_factories(
    *,
    stream_factory: Any | None = None,
    sync_response: Any | None = None,
) -> Iterator[None]:
    """Patch ``edu_agent.agent`` OpenAI factory functions so every new ``EduAgent`` gets fakes."""

    if sync_response is None:
        sync_response = make_sync_chat_response(
            [make_sync_choice(content="[test summary]", finish_reason="stop")],
            prompt_tokens=10,
            completion_tokens=2,
        )

    if stream_factory is None:

        async def _default_sf(*_a: Any, **_k: Any) -> Any:
            async def _g() -> AsyncIterator[Any]:
                async for c in async_iter_text_response("ok"):
                    yield c

            return _g()

        stream_factory = _default_sf

    def _async_build(_rt: Any) -> Any:
        ac = MagicMock()
        chat = MagicMock()
        completions = MagicMock()
        completions.create = AsyncMock(side_effect=stream_factory)
        chat.completions = completions
        ac.chat = chat
        return ac

    def _sync_build(_rt: Any) -> Any:
        sc = MagicMock()
        sc.chat = MagicMock()
        sc.chat.completions = MagicMock()
        sc.chat.completions.create = MagicMock(return_value=sync_response)
        return sc

    with (
        patch("edu_agent.agent.build_async_openai_client", side_effect=_async_build),
        patch("edu_agent.agent.build_openai_client", side_effect=_sync_build),
    ):
        yield


def last_async_create_messages(agent: Any) -> list[dict[str, Any]]:
    """``messages`` kw from the most recent async ``chat.completions.create`` call."""
    m = agent._async_client.chat.completions.create
    if not m.call_args:
        return []
    _args, kwargs = m.call_args
    return list(kwargs.get("messages") or [])
