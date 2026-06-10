"""
协议适配器抽象基类。

设计目标：
- 每个协议（OpenAI / Gemini / Cohere 等）实现一个 ProtocolAdapter
- 非流式：encode_request / decode_response 是纯函数
- 流式：每个连接一个 StreamState 实例，零拷贝转换
- 加新协议不影响 router.py 主体逻辑
"""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass
class AnthropicEvent:
    """Anthropic SSE 事件（协议转换的目标格式）。

    router.py 直接把它序列化成 `event: ...\\ndata: ...\\n\\n` 字节流回给 Claude Code。
    """
    event: str  # "message_start" / "content_block_start" / ...
    data: dict

    def to_sse(self) -> bytes:
        import json
        return f"event: {self.event}\ndata: {json.dumps(self.data, ensure_ascii=False)}\n\n".encode("utf-8")


class StreamState(abc.ABC):
    """流式响应状态机：把上游协议的 chunk 累积/翻译成 Anthropic 事件。

    每个 HTTP 连接一个实例，无锁。router.py 在 handler 里 new 出来。
    """
    @abc.abstractmethod
    def feed(self, chunk: bytes) -> list[AnthropicEvent]:
        """上游到达一段原始字节，返回需要立即吐给客户端的 Anthropic 事件列表。"""
        ...

    def flush(self) -> list[AnthropicEvent]:
        """上游流结束时（HTTP body 读完后）调用，返回剩余事件（一般是 message_stop）。"""
        return []


class ProtocolAdapter(abc.ABC):
    """协议适配器：把 Anthropic 协议的请求/响应翻译成目标协议。

    router.py 启动时通过 ROUTER_PROTOCOL=xxx 选定一个 adapter（全程单例）。
    """
    name: str  # "openai" / "gemini" / ...

    @abc.abstractmethod
    def encode_request(self, anthropic_body: dict) -> dict:
        """Anthropic /v1/messages body → 目标协议的 request body。"""
        ...

    @abc.abstractmethod
    def decode_response(self, response_body: dict) -> dict:
        """目标协议的 response body → Anthropic /v1/messages response body。"""
        ...

    @abc.abstractmethod
    def upstream_path(self) -> str:
        """目标协议的上游端点路径（如 OpenAI 的 /v1/chat/completions）。"""
        ...

    @abc.abstractmethod
    def upstream_headers(self, token: str) -> dict[str, str]:
        """目标协议需要的请求头。"""
        ...

    def new_stream_state(self, model: str) -> StreamState:
        """每个流式连接 new 一个状态机。默认抛错，adapter 必须显式实现才能支持 stream。"""
        raise NotImplementedError(f"{self.name} adapter 不支持流式响应")


# ──────────────────────────────────────────
# 全局注册表（放在 base.py 避免与具体 adapter 的循环 import）
# ──────────────────────────────────────────

REGISTRY: dict[str, ProtocolAdapter] = {}


def register(adapter: ProtocolAdapter) -> None:
    """adapter 通过装饰器 @register_adapter 注册自己。"""
    if adapter.name in REGISTRY:
        raise ValueError(f"协议 {adapter.name!r} 已被注册")
    REGISTRY[adapter.name] = adapter


def get(name: str) -> ProtocolAdapter:
    if name not in REGISTRY:
        raise KeyError(f"未知协议: {name!r}，可用: {list(REGISTRY.keys())}")
    return REGISTRY[name]


def register_adapter(cls: type[ProtocolAdapter]) -> type[ProtocolAdapter]:
    """类装饰器：在类定义时自动注册到 REGISTRY。"""
    register(cls())
    return cls

