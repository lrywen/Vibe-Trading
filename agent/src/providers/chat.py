"""ChatLLM: raw LLM message interface with function calling support.

ChatLLM is designed specifically for the AgentLoop ReAct cycle.
"""

from __future__ import annotations

import logging
import time
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.providers.llm import build_llm


def _is_engine_busy_error(e: Exception) -> bool:
    """Check if the error indicates the LLM engine is busy."""
    error_str = str(e).lower()
    return any(keyword in error_str for keyword in [
        "engine busy", 
        "recvfromengineerror",
        "one_api_error",
        "code: 10010",
        "server overloaded",
        "service unavailable"
    ])

# Module-level logger
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
logger.setLevel(logging.DEBUG)


def _dedupe_finish_reason(raw: str) -> str:
    """Relays (OpenRouter) emit finish_reason per chunk; AIMessageChunk.__add__
    concatenates into 'stopstop', 'tool_callstool_calls', etc. Return the
    canonical suffix so ReAct equality checks survive.
    """
    return next(
        (m for m in ("tool_calls", "function_call", "content_filter", "length", "stop")
         if raw.endswith(m)),
        raw,
    )


@dataclass
class ToolCallRequest:
    """Tool call request returned by the LLM.

    Attributes:
        id: Tool call ID (used to match tool_result messages).
        name: Tool name.
        arguments: Tool argument dict.
        thought_signature: Gemini thinking-model signature to echo on the next turn.
    """

    id: str
    name: str
    arguments: Dict[str, Any]
    thought_signature: Optional[str] = None


@dataclass
class LLMResponse:
    """LLM response.

    Attributes:
        content: Text content (final answer or thinking text).
        tool_calls: List of tool call requests.
        reasoning_content: Optional thinking trace surfaced by reasoning models.
        finish_reason: Finish reason string.
        usage_metadata: Real token counts reported by the provider, when
            available. Mirrors LangChain's ``AIMessage.usage_metadata`` —
            ``{"input_tokens": int, "output_tokens": int, "total_tokens": int}``.
            ``None`` if the provider did not return usage information; callers
            should fall back to a heuristic in that case.
    """

    content: Optional[str] = None
    tool_calls: List[ToolCallRequest] = field(default_factory=list)
    reasoning_content: Optional[str] = None
    finish_reason: str = "stop"
    usage_metadata: Optional[Dict[str, int]] = None

    @property
    def has_tool_calls(self) -> bool:
        """Return True if the response contains tool calls."""
        return len(self.tool_calls) > 0


class ChatLLM:
    """LLM chat client with function calling support.

    Uses build_llm() to obtain a ChatOpenAI instance and bind_tools() to attach tool definitions.

    Attributes:
        model_name: Model name.
    """

    def __init__(self, model_name: Optional[str] = None) -> None:
        """Initialize ChatLLM.

        Args:
            model_name: Model name; defaults to the environment variable value.
        """
        logger.info("Initializing ChatLLM with model_name=%s", model_name)
        self.model_name = model_name
        self._llm = build_llm(model_name=model_name)
        logger.info("✅ ChatLLM initialized — underlying LLM=%s",
                    getattr(self._llm, "model_name", "unknown"))

    def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None, timeout: Optional[int] = None, max_retries: int = 3) -> LLMResponse:
        """Call the LLM synchronously with retry logic for engine busy errors.

        Args:
            messages: Message list (OpenAI format).
            tools: Tool definition list (OpenAI function calling format).
            timeout: Optional per-call timeout in seconds.
            max_retries: Maximum number of retries for engine busy errors.

        Returns:
            LLMResponse.
        """
        logger.info("▶ chat() called — messages=%d, tools=%d, timeout=%s, max_retries=%d",
                    len(messages) if messages else 0,
                    len(tools) if tools else 0,
                    timeout,
                    max_retries)

        # Summarize messages (not logging full content to avoid leaking sensitive data)
        for i, m in enumerate(messages or []):
            role = m.get("role") if isinstance(m, dict) else getattr(m, "type", "unknown")
            logger.debug("  msg[%d] role=%s", i, role)

        llm = self._llm.bind_tools(tools) if tools else self._llm
        if tools:
            tool_names = [t.get("name", t.get("function", {}).get("name", "?")) if isinstance(t, dict) else "?" for t in tools]
            logger.info("  Tools bound: %s", tool_names)

        config = {"timeout": timeout} if timeout else {}
        logger.debug("  Config: %s", config)

        start = time.time()
        last_exception = None
        
        for attempt in range(max_retries + 1):
            try:
                ai_message = llm.invoke(messages, config=config)
                elapsed = time.time() - start
                logger.info("✔ LLM invoke completed in %.2fs (attempt %d/%d)", 
                            elapsed, attempt + 1, max_retries + 1)
                response = self._parse_response(ai_message)
                logger.info("  Response: content=%s, tool_calls=%d, reason=%s, usage=%s",
                            (response.content[:80] + "…") if response.content and len(str(response.content)) > 80 else str(response.content),
                            len(response.tool_calls),
                            response.finish_reason,
                            response.usage_metadata)
                return response
            except Exception as e:
                last_exception = e
                elapsed = time.time() - start
                
                if attempt < max_retries and _is_engine_busy_error(e):
                    # Exponential backoff with jitter for engine busy errors
                    wait_time = (2 ** attempt) + random.uniform(0, 1)
                    logger.warning("⚠️  LLM engine busy (attempt %d/%d) — retrying in %.2fs: %s",
                                   attempt + 1, max_retries + 1, wait_time, str(e)[:100])
                    time.sleep(wait_time)
                else:
                    logger.exception("✘ LLM invoke FAILED after %.2fs (attempt %d/%d): %s (type=%s)",
                                     elapsed, attempt + 1, max_retries + 1, e, type(e).__name__)
                    raise
        
        # This should never be reached due to the loop, but just in case
        logger.error("✘ All %d retry attempts failed", max_retries + 1)
        raise last_exception

    def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        on_text_chunk: Optional[Any] = None,
        timeout: Optional[int] = None,
        max_retries: int = 2,
    ) -> LLMResponse:
        """Stream the LLM and optionally forward text deltas (e.g. thinking).

        Iterates AIMessageChunk; each text delta invokes ``on_text_chunk``.
        Aggregates chunks into one response; on failure due to engine busy, retries; 
        other failures fall back to ``chat()``.

        Args:
            messages: Messages in OpenAI format.
            tools: Tool definitions for function calling.
            on_text_chunk: Optional callback ``(delta: str) -> None``.
            timeout: Optional per-call timeout in seconds.
            max_retries: Maximum number of retries for engine busy errors.

        Returns:
            Parsed ``LLMResponse``.
        """
        logger.info("▶ stream_chat() called — messages=%d, tools=%d, timeout=%s, on_text_chunk=%s, max_retries=%d",
                    len(messages) if messages else 0,
                    len(tools) if tools else 0,
                    timeout,
                    "yes" if on_text_chunk else "no",
                    max_retries)

        last_exception = None
        
        for attempt in range(max_retries + 1):
            try:
                llm = self._llm.bind_tools(tools) if tools else self._llm
                if tools:
                    tool_names = [t.get("name", t.get("function", {}).get("name", "?")) if isinstance(t, dict) else "?" for t in tools]
                    logger.debug("  Tools bound: %s", tool_names)
                config = {"timeout": timeout} if timeout else {}
                logger.debug("  Config: %s", config)

                accumulated = None
                chunk_count = 0
                total_content_len = 0
                start = time.time()

                for chunk in llm.stream(messages, config=config):
                    chunk_count += 1
                    if chunk.content and on_text_chunk:
                        total_content_len += len(chunk.content or "")
                        on_text_chunk(chunk.content)
                    accumulated = chunk if accumulated is None else accumulated + chunk

                    if chunk_count == 1:
                        logger.debug("  First chunk received — first_token_latency=%.2fs", time.time() - start)

                elapsed = time.time() - start
                logger.info("✔ Stream completed — %d chunks, %.2fs, total_text=%d chars (attempt %d/%d)",
                            chunk_count, elapsed, total_content_len, attempt + 1, max_retries + 1)

                if accumulated is None:
                    logger.warning("  No chunks received → returning empty response")
                    return LLMResponse(content="", tool_calls=[], finish_reason="stop")

                response = self._parse_response(accumulated)
                logger.info("  Response: tool_calls=%d, reason=%s, usage=%s",
                            len(response.tool_calls), response.finish_reason,
                            response.usage_metadata)
                return response
            except Exception as e:
                last_exception = e
                
                if attempt < max_retries and _is_engine_busy_error(e):
                    # Exponential backoff with jitter for engine busy errors
                    wait_time = (2 ** attempt) + random.uniform(0, 1)
                    logger.warning("⚠️  LLM engine busy (attempt %d/%d) — retrying stream in %.2fs: %s",
                                   attempt + 1, max_retries + 1, wait_time, str(e)[:100])
                    time.sleep(wait_time)
                else:
                    logger.exception("✘ stream_chat failed after %.2fs (attempt %d/%d): %s — falling back to chat()",
                                     time.time() - start if 'start' in locals() else 0, 
                                     attempt + 1, max_retries + 1, e)
                    return self.chat(messages, tools=tools, timeout=timeout)
        
        # This should never be reached due to the loop, but just in case
        logger.error("✘ All %d stream retry attempts failed", max_retries + 1)
        return self.chat(messages, tools=tools, timeout=timeout)

    @staticmethod
    def _tool_call_thought_signature_maps(ai_message: Any) -> tuple[dict[str, str], dict[int, str]]:
        """Return Gemini thought signatures captured by ``ChatOpenAIWithReasoning``."""
        by_id: dict[str, str] = {}
        by_index: dict[int, str] = {}
        additional_kwargs = getattr(ai_message, "additional_kwargs", {})
        entries = additional_kwargs.get("tool_call_thought_signatures", [])

        if isinstance(entries, dict):
            entries = [entries]
        if not isinstance(entries, list):
            return by_id, by_index

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            signature = entry.get("thought_signature")
            if not signature:
                continue
            if entry.get("id"):
                by_id[str(entry["id"])] = signature
            index = entry.get("index")
            if isinstance(index, int):
                by_index[index] = signature
        return by_id, by_index

    @staticmethod
    def _parse_response(ai_message: Any) -> LLMResponse:
        """Convert a LangChain AIMessage (or AIMessageChunk) to ``LLMResponse``.

        Single source for reasoning: ``additional_kwargs["reasoning_content"]``,
        populated by ``ChatOpenAIWithReasoning`` on both stream and non-stream paths.

        ``usage_metadata`` is forwarded as-is from the underlying message so
        downstream cost / billing audit code (e.g. swarm worker token totals)
        can use real provider tokens instead of a character-count heuristic.
        For ``AIMessageChunk`` the metadata accumulates via the ``__add__``
        merge LangChain performs while the response is being streamed; the
        final aggregate carries the same shape as the non-stream path.
        """
        usage = getattr(ai_message, "usage_metadata", None)
        # Some providers / older LangChain versions surface a ``UsageMetadata``
        # TypedDict that doesn't json-serialise without a cast. Normalise to a
        # plain ``dict[str, int]`` so the value can be persisted alongside the
        # rest of the run state without surprises.
        if usage is not None and not isinstance(usage, dict):
            try:
                usage = dict(usage)
            except (TypeError, ValueError):
                usage = None
        thought_signatures_by_id, thought_signatures_by_index = (
            ChatLLM._tool_call_thought_signature_maps(ai_message)
        )
        return LLMResponse(
            content=ai_message.content,
            tool_calls=[
                ToolCallRequest(
                    id=tc["id"],
                    name=tc["name"],
                    arguments=tc["args"],
                    thought_signature=thought_signatures_by_id.get(str(tc["id"]))
                    or thought_signatures_by_index.get(index),
                )
                for index, tc in enumerate(ai_message.tool_calls)
            ],
            reasoning_content=ai_message.additional_kwargs.get("reasoning_content"),
            finish_reason=_dedupe_finish_reason(
                ai_message.response_metadata.get("finish_reason", "stop")
            ),
            usage_metadata=usage,
        )
