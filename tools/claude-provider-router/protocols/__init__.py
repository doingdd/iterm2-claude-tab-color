"""
协议适配器注册表。

新加协议（如 gemini、cohere）的步骤：
1. 在 protocols/ 下新建 <name>_adapter.py
2. 实现 ProtocolAdapter 接口，用 @register_adapter 装饰
3. 在下面加一行：from . import <name>_adapter  # noqa: F401 — 触发 register()
4. router.py 配置 ROUTER_PROTOCOL=<name> 即可启用
"""

from .base import (
    ProtocolAdapter,
    StreamState,
    AnthropicEvent,
    REGISTRY,
    register,
    register_adapter,
    get,
)
from . import openai_adapter  # noqa: F401 — 触发 register()


__all__ = [
    "ProtocolAdapter", "StreamState", "AnthropicEvent",
    "REGISTRY", "register", "register_adapter", "get",
]
