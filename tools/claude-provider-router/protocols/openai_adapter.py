"""
OpenAI Chat Completions 协议适配器。

把 Anthropic /v1/messages 请求 → OpenAI /v1/chat/completions 请求，
把 OpenAI 响应（含 SSE 流）→ Anthropic /v1/messages 响应（含 SSE 流）。

MVP 支持：
- text / image_url / tool_use / tool_result 双向转换
- 文本 + 工具调用流式（含 tool_calls 分片累积）
- OpenAI usage 映射到 Anthropic usage

延后（标 TODO）：
- prompt caching 字段映射
- extended thinking / reasoning_effort
- 图片 base64 之外的传输方式
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from .base import AnthropicEvent, ProtocolAdapter, StreamState, register_adapter

logger = logging.getLogger(__name__)

# Anthropic stop_reason 映射
_STOP_REASON_MAP = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
    "function_call": "tool_use",  # 老 OpenAI 协议
}


# ──────────────────────────────────────────
# Request 转换：Anthropic → OpenAI
# ──────────────────────────────────────────

def _convert_system(system: Any) -> list[dict] | None:
    """Anthropic 顶级 system 字段（str 或 list）→ OpenAI messages[0]."""
    if not system:
        return None
    if isinstance(system, str):
        return [{"role": "system", "content": system}]
    # list 形式：[{"type": "text", "text": "...", "cache_control": {...}}]
    parts: list[dict] = []
    for block in system:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append({"role": "system", "content": block.get("text", "")})
        elif isinstance(block, str):
            parts.append({"role": "system", "content": block})
    return parts or None


def _convert_content_block(block: dict) -> dict | list[dict] | None:
    """Anthropic content block → OpenAI content part."""
    btype = block.get("type")
    if btype == "text":
        return {"type": "text", "text": block.get("text", "")}
    if btype == "image":
        source = block.get("source", {})
        stype = source.get("type")
        if stype == "base64":
            media_type = source.get("media_type", "image/png")
            data = source.get("data", "")
            return {
                "type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{data}"},
            }
        if stype == "url":
            return {
                "type": "image_url",
                "image_url": {"url": source.get("url", "")},
            }
        logger.warning(f"不支持的 image source type: {stype!r}，跳过")
        return None
    if btype == "tool_use":
        # 在 assistant message 里处理，这里不应该单独出现
        logger.warning("tool_use 出现在非 assistant 角色，忽略")
        return None
    if btype == "tool_result":
        # 同样在 user message 里处理
        return None
    logger.warning(f"未知 Anthropic content block type: {btype!r}，跳过")
    return None


def _convert_user_content(content: Any) -> Any:
    """user message content: str / list[block] / list[tool_result block] → OpenAI 格式。

    关键：Anthropic 的 tool_result 块要转成 OpenAI 的 role=tool 消息（不是 user 的 content 里）。
    所以这个函数返回 (user_parts, tool_messages)，调用方按角色分发。
    """
    user_parts: list[dict] = []
    tool_messages: list[dict] = []
    if isinstance(content, str):
        return [content], []
    for block in content or []:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "tool_result":
            tr_content = block.get("content", "")
            if isinstance(tr_content, list):
                # tool_result.content 可能是 list[block]
                tr_text = ""
                for tb in tr_content:
                    if isinstance(tb, dict) and tb.get("type") == "text":
                        tr_text += tb.get("text", "")
                tr_content = tr_text
            tool_messages.append({
                "role": "tool",
                "tool_call_id": block.get("tool_use_id", ""),
                "content": tr_content if isinstance(tr_content, str) else json.dumps(tr_content, ensure_ascii=False),
            })
        else:
            converted = _convert_content_block(block)
            if converted is None:
                continue
            if isinstance(converted, list):
                user_parts.extend(converted)
            else:
                user_parts.append(converted)
    return user_parts, tool_messages


def _convert_assistant_content(content: Any) -> tuple[Any, list[dict] | None]:
    """assistant message content → (text_or_null, tool_calls or None)。"""
    if isinstance(content, str):
        return content, None
    text_parts: list[str] = []
    tool_calls: list[dict] = []
    for block in content or []:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text_parts.append(block.get("text", ""))
        elif btype == "tool_use":
            tool_calls.append({
                "id": block.get("id", f"toolu_{uuid.uuid4().hex[:24]}"),
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                },
            })
    text = "".join(text_parts) or None
    return text, (tool_calls or None)


def _convert_tool_choice(tc: Any) -> Any:
    """Anthropic tool_choice → OpenAI tool_choice."""
    if not tc:
        return None
    if isinstance(tc, str):
        return tc
    if isinstance(tc, dict):
        t = tc.get("type")
        if t == "auto":
            return "auto"
        if t == "any":
            return "required"
        if t == "tool":
            name = tc.get("name")
            if name:
                return {"type": "function", "function": {"name": name}}
        if t == "none":
            return "none"
    return None


def _convert_tools(tools: list[dict] | None) -> list[dict] | None:
    if not tools:
        return None
    out: list[dict] = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        out.append({
            "type": "function",
            "function": {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {}),
            },
        })
    return out or None


def _strip_anthropic_only_fields(body: dict) -> dict:
    """去掉 OpenAI 协议不识别的 Anthropic 字段，避免 vLLM validation 失败。"""
    skip = {
        "metadata", "stop_sequences", "betas", "context_management",
        "mcp_servers", "container", "inference_geo", "output_config",
        "speed", "tool_reference_blocks", "extra", "anthropic_internal",
    }
    return {k: v for k, v in body.items() if k not in skip}


# ──────────────────────────────────────────
# Response 转换：OpenAI → Anthropic
# ──────────────────────────────────────────

def _convert_openai_message_to_anthropic_content(msg: dict) -> list[dict]:
    """OpenAI choices[0].message → Anthropic content blocks。"""
    blocks: list[dict] = []
    text = msg.get("content")
    if text:
        blocks.append({"type": "text", "text": text})
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function", {})
        args_raw = fn.get("arguments", "")
        try:
            inp = json.loads(args_raw) if args_raw else {}
        except json.JSONDecodeError:
            logger.warning(f"tool arguments 不是合法 JSON: {args_raw[:100]!r}，按空对象处理")
            inp = {}
        blocks.append({
            "type": "tool_use",
            "id": tc.get("id", f"toolu_{uuid.uuid4().hex[:24]}"),
            "name": fn.get("name", ""),
            "input": inp,
        })
    if not blocks:
        blocks.append({"type": "text", "text": ""})
    return blocks


# ──────────────────────────────────────────
# StreamState：OpenAI SSE → Anthropic SSE
# ──────────────────────────────────────────

class OpenAIToAnthropicStreamState(StreamState):
    """OpenAI 增量 chunk → Anthropic SSE 事件的状态机。

    关键字段：
    - message_started: bool — message_start 是否已发
    - block_index: int — 当前 content_block 索引
    - block_open: None | "text" | "tool" — 当前 block 状态
    - tool_blocks: dict[int, dict] — 按 OpenAI tc_index 追踪
    - finish_reason: str | None — 累积的结束原因
    - usage: dict | None — 累积的 usage
    """

    def __init__(self, model: str):
        self.model = model
        self.message_started = False
        self.block_index = 0
        self.block_open: str | None = None
        self.tool_blocks: dict[int, dict] = {}
        self.finish_reason: str | None = None
        self.usage: dict | None = None
        self._closed = False

    def feed(self, chunk: bytes) -> list[AnthropicEvent]:
        if self._closed:
            return []
        out: list[AnthropicEvent] = []
        try:
            text = chunk.decode("utf-8", errors="replace")
        except Exception as e:
            logger.warning(f"chunk 解码失败: {e}")
            return out
        for line in text.split("\n"):
            if not line.startswith("data: "):
                continue
            payload_str = line[6:].strip()
            if not payload_str:
                continue
            if payload_str == "[DONE]":
                out.extend(self._finalize())
                return out
            try:
                payload = json.loads(payload_str)
            except json.JSONDecodeError:
                logger.warning(f"非法 JSON chunk 跳过: {payload_str[:200]}")
                continue
            # usage 可能在最后一个 chunk 里（stream_options.include_usage=true）
            if payload.get("usage"):
                self.usage = payload["usage"]
            for choice in payload.get("choices", []):
                out.extend(self._handle_choice(choice))
        return out

    def flush(self) -> list[AnthropicEvent]:
        if self._closed:
            return []
        return self._finalize()

    # ── 内部方法 ──

    def _handle_choice(self, choice: dict) -> list[AnthropicEvent]:
        out: list[AnthropicEvent] = []
        delta = choice.get("delta", {}) or {}
        finish = choice.get("finish_reason")

        # 1. 第一个 delta → message_start
        if not self.message_started:
            self.message_started = True
            in_tok = (self.usage or {}).get("prompt_tokens", 0)
            out.append(AnthropicEvent("message_start", {
                "type": "message_start",
                "message": {
                    "id": f"msg_{uuid.uuid4().hex[:24]}",
                    "type": "message",
                    "role": "assistant",
                    "model": self.model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": in_tok,
                        "output_tokens": 0,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                },
            }))

        # 2. 文本片段
        if delta.get("content"):
            if self.block_open != "text":
                out.extend(self._close_block())
                out.append(AnthropicEvent("content_block_start", {
                    "type": "content_block_start",
                    "index": self.block_index,
                    "content_block": {"type": "text", "text": ""},
                }))
                self.block_open = "text"
            out.append(AnthropicEvent("content_block_delta", {
                "type": "content_block_delta",
                "index": self.block_index,
                "delta": {"type": "text_delta", "text": delta["content"]},
            }))

        # 3. tool_calls（按 tc_index 索引）
        for tc in delta.get("tool_calls") or []:
            idx = tc.get("index", 0)
            fn = tc.get("function", {}) or {}
            if idx not in self.tool_blocks:
                # 新 tool_use：先关当前 block，再开 tool block
                out.extend(self._close_block())
                self.block_index += 1
                tool_id = tc.get("id") or f"toolu_{uuid.uuid4().hex[:24]}"
                tool_name = fn.get("name", "")
                self.tool_blocks[idx] = {
                    "id": tool_id,
                    "name": tool_name,
                    "input_acc": "",
                }
                self.block_open = "tool"
                out.append(AnthropicEvent("content_block_start", {
                    "type": "content_block_start",
                    "index": self.block_index,
                    "content_block": {
                        "type": "tool_use",
                        "id": tool_id,
                        "name": tool_name,
                        "input": {},
                    },
                }))
            # 累积 arguments 分片
            if fn.get("arguments"):
                self.tool_blocks[idx]["input_acc"] += fn["arguments"]
                out.append(AnthropicEvent("content_block_delta", {
                    "type": "content_block_delta",
                    "index": self.block_index,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": fn["arguments"],
                    },
                }))

        # 4. 结束原因
        if finish:
            out.extend(self._close_block())
            self.finish_reason = _STOP_REASON_MAP.get(finish, "end_turn")
        return out

    def _close_block(self) -> list[AnthropicEvent]:
        if self.block_open is None:
            return []
        out = [AnthropicEvent("content_block_stop", {
            "type": "content_block_stop",
            "index": self.block_index,
        })]
        # 索引递增在 open 新 block 时做（text → tool 时也 +1）
        self.block_open = None
        return out

    def _advance_block_index(self) -> None:
        """block 关闭后递增 index，给下一个 block 用。"""
        self.block_index += 1

    def _finalize(self) -> list[AnthropicEvent]:
        if self._closed:
            return []
        self._closed = True
        out: list[AnthropicEvent] = []
        # 关闭可能还开着的 block
        if self.block_open is not None:
            out.extend(self._close_block())
        # 任何 tool block 还没 close 的（极端 case）也要关
        for idx, tb in self.tool_blocks.items():
            # 如果没 close 过，_close_block 已经处理了
            pass
        # message_delta（带 stop_reason + usage）
        out_tok = (self.usage or {}).get("completion_tokens", 0)
        out.append(AnthropicEvent("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": self.finish_reason or "end_turn", "stop_sequence": None},
            "usage": {
                "output_tokens": out_tok,
                "input_tokens": (self.usage or {}).get("prompt_tokens", 0),
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        }))
        out.append(AnthropicEvent("message_stop", {"type": "message_stop"}))
        return out


# ──────────────────────────────────────────
# Adapter 注册
# ──────────────────────────────────────────

@register_adapter
class OpenAIAdapter(ProtocolAdapter):
    """OpenAI Chat Completions 协议适配器。

    注意：vLLM 等部分 provider 在 OpenAI 响应里塞了 `provider_specific_fields`（嵌套对象），
    不属于标准 OpenAI 协议。我们的 decode 只挑 `choices[].message.{content, tool_calls}`、
    `usage` 等已知字段，嵌套的额外字段会被自然忽略。
    """
    name = "openai"

    def encode_request(self, anthropic_body: dict) -> dict:
        body = _strip_anthropic_only_fields(anthropic_body)
        out: dict[str, Any] = {"model": body.get("model", "")}

        # max_tokens → max_completion_tokens（OpenAI 新名）/ max_tokens（兼容）
        if "max_tokens" in body:
            out["max_tokens"] = body["max_tokens"]
        # temperature / top_p / stream 直传
        for k in ("temperature", "top_p", "stream", "stop", "presence_penalty", "frequency_penalty", "user", "seed"):
            if k in body:
                out[k] = body[k]

        # system 顶级字段
        sys_msgs = _convert_system(body.get("system"))

        # messages
        out_messages: list[dict] = []
        if sys_msgs:
            out_messages.extend(sys_msgs)
        for m in body.get("messages", []):
            role = m.get("role")
            content = m.get("content")
            if role == "user":
                user_parts, tool_msgs = _convert_user_content(content)
                # tool_result 块要变成独立 tool role 消息
                if user_parts:
                    # 决定 content 形态：str / dict / list
                    if len(user_parts) == 1 and isinstance(user_parts[0], str):
                        # 简单文本 "hi" → 直接传字符串（OpenAI 也支持）
                        content_out: Any = user_parts[0]
                    elif (len(user_parts) == 1
                          and isinstance(user_parts[0], dict)
                          and user_parts[0].get("type") == "text"):
                        # 单个 text block → 提取文本
                        content_out = user_parts[0].get("text", "")
                    else:
                        # 多模态 → list
                        content_out = user_parts
                    out_messages.append({"role": "user", "content": content_out})
                for tm in tool_msgs:
                    out_messages.append(tm)
            elif role == "assistant":
                text, tool_calls = _convert_assistant_content(content)
                msg: dict[str, Any] = {"role": "assistant"}
                if text is not None:
                    msg["content"] = text
                else:
                    msg["content"] = None
                if tool_calls:
                    msg["tool_calls"] = tool_calls
                out_messages.append(msg)
            else:
                logger.warning(f"未知 message role: {role!r}，跳过")
        out["messages"] = out_messages

        # tools
        if (tools := _convert_tools(body.get("tools"))):
            out["tools"] = tools
        # tool_choice
        if (tc := _convert_tool_choice(body.get("tool_choice"))):
            out["tool_choice"] = tc

        return out

    def decode_response(self, response_body: dict) -> dict:
        """OpenAI chat completion（非流式）→ Anthropic message response。"""
        choices = response_body.get("choices", [])
        if not choices:
            # 错误或空响应
            return {
                "id": response_body.get("id", f"msg_{uuid.uuid4().hex[:24]}"),
                "type": "message",
                "role": "assistant",
                "model": response_body.get("model", ""),
                "content": [],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0,
                          "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
            }
        choice = choices[0]
        msg = choice.get("message", {}) or {}
        finish = choice.get("finish_reason")
        content = _convert_openai_message_to_anthropic_content(msg)
        usage = response_body.get("usage", {}) or {}
        return {
            "id": response_body.get("id", f"msg_{uuid.uuid4().hex[:24]}"),
            "type": "message",
            "role": "assistant",
            "model": response_body.get("model", ""),
            "content": content,
            "stop_reason": _STOP_REASON_MAP.get(finish or "", "end_turn"),
            "stop_sequence": None,
            "usage": {
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        }

    def upstream_path(self) -> str:
        return "/v1/chat/completions"

    def upstream_headers(self, token: str) -> dict[str, str]:
        return {
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
        }

    def new_stream_state(self, model: str) -> StreamState:
        return OpenAIToAnthropicStreamState(model)
