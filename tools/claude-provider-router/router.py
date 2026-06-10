#!/usr/bin/env python3
"""
Claude Code 多 Provider 路由代理（支持两种模式）

**Team 模式**（默认，基于 auth token 区分 leader / teammate）：
  - leader 进程的 apiKeyHelper 返回 "leader-token"
  - teammate 进程的 apiKeyHelper 返回 "teammate-token"（因为 CLAUDE_TEAM_ROLE 被 claude 过滤）
  - 路由器检查请求中的 Authorization header 来决定路由

  环境变量：
    ROUTER_LEADER_URL=...      ROUTER_LEADER_TOKEN=...
    ROUTER_TEAMMATE_URL=...    ROUTER_TEAMMATE_TOKEN=...
    ROUTER_TEAMMATE_MODEL=...  # 转发给 teammate 时替换的模型名（可选）

**Protocol 模式**（通过 ROUTER_PROTOCOL 启用）：
  把 Anthropic /v1/messages 请求翻译成目标协议（OpenAI / Gemini 等），转发给上游，
  再把响应翻译回 Anthropic 格式。多 c 进程共享同一个 router 实例。

  环境变量：
    ROUTER_PROTOCOL=openai        # 启用协议转换模式（用 protocols/REGISTRY 中的 adapter）
    ROUTER_UPSTREAM_URL=...       # 目标 provider base URL（可带 /v1 后缀，会被剥掉）
    ROUTER_UPSTREAM_TOKEN=...     # 目标 provider 真实 token
    ROUTER_TEAMMATE_MODEL=...     # 强制替换的 model 名（沿用旧 var 名）

启动方式（由 c 脚本调用）：
  uvicorn router:app --port 3100    # team 模式
  uvicorn router:app --port 4100    # protocol 模式
"""

import os
import sys
import json
import time
import logging
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse, Response

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI()

# ──────────────────────────────────────────
# 模式选择（启动时一次性决定，两种模式互斥）
# ──────────────────────────────────────────

ROUTER_PROTOCOL    = os.environ.get("ROUTER_PROTOCOL", "").lower()
ROUTER_UPSTREAM_URL = ""  # 仅 protocol 模式用
ROUTER_UPSTREAM_TOKEN = ""  # 仅 protocol 模式用
ROUTER_MODEL_OVERRIDE = ""  # 仅 protocol 模式用

if ROUTER_PROTOCOL:
    # protocol 模式：把 protocols/ 加入 path 后 import
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from protocols import get as get_adapter  # noqa: E402
    try:
        ADAPTER = get_adapter(ROUTER_PROTOCOL)
    except KeyError as e:
        raise RuntimeError(f"未注册的协议: {e}")
    # URL 处理：去掉尾 / 和 /v1 后缀，避免拼成 /v1/v1/chat/completions
    ROUTER_UPSTREAM_URL = os.environ.get("ROUTER_UPSTREAM_URL", "").rstrip("/")
    if ROUTER_UPSTREAM_URL.endswith("/v1"):
        ROUTER_UPSTREAM_URL = ROUTER_UPSTREAM_URL[:-3]
    ROUTER_UPSTREAM_TOKEN = os.environ.get("ROUTER_UPSTREAM_TOKEN", "")
    ROUTER_MODEL_OVERRIDE = os.environ.get("ROUTER_TEAMMATE_MODEL", "")  # 沿用旧 var 名
    if not ROUTER_UPSTREAM_URL or not ROUTER_UPSTREAM_TOKEN:
        raise RuntimeError("protocol 模式需要 ROUTER_UPSTREAM_URL 和 ROUTER_UPSTREAM_TOKEN")
    MODE = "protocol"
    logger.info(f"启动 protocol 模式：protocol={ROUTER_PROTOCOL} upstream={ROUTER_UPSTREAM_URL}")
else:
    # team 模式
    LEADER_URL      = os.environ.get("ROUTER_LEADER_URL", "").rstrip("/")
    LEADER_TOKEN    = os.environ.get("ROUTER_LEADER_TOKEN", "")
    TEAMMATE_URL    = os.environ.get("ROUTER_TEAMMATE_URL", "").rstrip("/")
    TEAMMATE_TOKEN  = os.environ.get("ROUTER_TEAMMATE_TOKEN", "")
    TEAMMATE_MODEL  = os.environ.get("ROUTER_TEAMMATE_MODEL", "")
    ADAPTER = None
    MODE = "team"

# 部分 provider（如 MiniMax）不支持的 beta header
UNSUPPORTED_BETA_PREFIXES = [
    "interleaved-thinking",
    "output-128k",
]


# ──────────────────────────────────────────
# Team 模式 helper
# ──────────────────────────────────────────

def extract_role_token(request: Request) -> str:
    """从请求头中提取角色 token（apiKeyHelper 写入的）"""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    # 兜底：检查 x-api-key
    return request.headers.get("x-api-key", "").strip()


def resolve_route(request: Request, model: str = "") -> dict:
    """根据 auth token 路由：leader-token → leader, teammate-token → teammate"""
    role_token = extract_role_token(request)

    if role_token == "leader-token":
        return {"url": LEADER_URL, "token": LEADER_TOKEN, "role": "leader", "model_override": None}
    elif role_token == "teammate-token":
        return {"url": TEAMMATE_URL, "token": TEAMMATE_TOKEN, "role": "teammate", "model_override": TEAMMATE_MODEL or None}
    else:
        # 兜底：无法识别 token 时按 model name 路由（兼容旧逻辑）
        logger.warning(f"未知 token: {role_token!r}, 按 model 兜底路由")
        if model and ("opus" in model.lower()):
            return {"url": LEADER_URL, "token": LEADER_TOKEN, "role": "leader", "model_override": None}
        return {"url": TEAMMATE_URL, "token": TEAMMATE_TOKEN, "role": "teammate", "model_override": TEAMMATE_MODEL or None}


def build_headers(route: dict, request: Request) -> dict:
    headers = {
        "authorization":     f"Bearer {route['token']}",
        "anthropic-version": request.headers.get("anthropic-version", "2023-06-01"),
        "content-type":      "application/json",
    }
    for key, val in request.headers.items():
        if not key.lower().startswith("anthropic-beta"):
            continue
        if route["role"] == "teammate":
            skip = any(val.lower().find(prefix) >= 0 for prefix in UNSUPPORTED_BETA_PREFIXES)
            if skip:
                logger.info(f"过滤 beta header: {key}={val}")
                continue
        headers[key] = val
    return headers


def prepare_body(body: dict, route: dict) -> dict:
    result = {**body}
    if route["model_override"]:
        result["model"] = route["model_override"]
    if route["role"] == "teammate":
        for field in ("output_config",):
            if field in result:
                logger.info(f"过滤不支持字段: {field}")
                del result[field]
    return result


# ──────────────────────────────────────────
# Protocol 模式 helper
# ──────────────────────────────────────────

async def _dump_and_respond_error(status: int, content: bytes, request: dict) -> JSONResponse:
    """上游错误时记录到 .routers/failed_*.json，返回包装后的错误响应。"""
    logger.error(f"upstream error {status}: {content[:300]}")
    dump_path = os.path.join(os.path.dirname(__file__), ".routers", f"failed_{int(time.time()*1000)}.json")
    try:
        os.makedirs(os.path.dirname(dump_path), exist_ok=True)
        with open(dump_path, "w") as _f:
            json.dump({"status": status, "response": content.decode("utf-8", errors="replace"),
                       "request": request}, _f, ensure_ascii=False, indent=2)
        logger.error(f"完整请求体已写入: {dump_path}")
    except Exception:
        pass
    # 包装为 Anthropic 错误格式
    try:
        upstream_json = json.loads(content)
        if isinstance(upstream_json, dict) and "error" in upstream_json:
            # OpenAI 错误 {error: {message, type, code}} → Anthropic
            err = upstream_json["error"]
            msg = err.get("message", content.decode("utf-8", errors="replace")) if isinstance(err, dict) else str(err)
            anthropic_err = {"type": "error", "error": {"type": "api_error", "message": msg}}
            return JSONResponse(status_code=status, content=anthropic_err)
    except Exception:
        pass
    return JSONResponse(status_code=status, content={"type": "error", "error": {"type": "api_error", "message": content.decode("utf-8", errors="replace")}})


# ──────────────────────────────────────────
# 端点
# ──────────────────────────────────────────

@app.get("/")
async def health():
    if MODE == "protocol":
        return {
            "status": "ok",
            "mode": "protocol",
            "protocol": ROUTER_PROTOCOL,
            "upstream": ROUTER_UPSTREAM_URL,
        }
    return {
        "status": "ok",
        "mode": "team",
        "leader": LEADER_URL,
        "teammate": TEAMMATE_URL,
        "teammate_model": TEAMMATE_MODEL or "(透传原始 model)",
    }


# ── Team 模式：原 proxy_messages 改名 ──

async def team_handler(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json body")

    model = body.get("model", "")
    route = resolve_route(request, model)
    out_body = prepare_body(body, route)
    headers = build_headers(route, request)

    tag = "🔵 LEADER" if route["role"] == "leader" else "🟢 TEAMMATE"
    msg = f"{tag} model={model!r}"
    if route["model_override"]:
        msg += f" → {out_body['model']!r}"
    upstream_host = route["url"].replace("https://", "").replace("http://", "").split("/")[0]
    msg += f" → {upstream_host}"
    logger.info(msg)

    upstream_url = f"{route['url']}/v1/messages"
    is_stream = body.get("stream", False)

    if is_stream:
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                async with client.stream("POST", upstream_url, headers=headers, json=out_body) as resp:
                    status = resp.status_code
                    content = await resp.aread()
        except Exception as e:
            logger.error(f"request error: {e}")
            raise HTTPException(status_code=502, detail=str(e))

        if status != 200:
            return await _dump_and_respond_error(status, content, out_body)

        return Response(content=content, media_type="text/event-stream", status_code=200)
    else:
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                resp = await client.post(upstream_url, headers=headers, json=out_body)
            return JSONResponse(status_code=resp.status_code, content=resp.json())
        except Exception as e:
            logger.error(f"request error: {e}")
            raise HTTPException(status_code=502, detail=str(e))


# ── Protocol 模式：Anthropic↔目标协议转换 ──

async def protocol_handler(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json body")

    # 1. 编码：Anthropic body → 目标协议 body
    try:
        out_body = ADAPTER.encode_request(body)
    except Exception as e:
        logger.error(f"encode_request 失败: {e}")
        raise HTTPException(status_code=400, detail=f"encode error: {e}")

    # 2. 强制 model override（让 router 可以替换 model 名）
    if ROUTER_MODEL_OVERRIDE:
        out_body["model"] = ROUTER_MODEL_OVERRIDE

    # 3. 上游请求头
    headers = ADAPTER.upstream_headers(ROUTER_UPSTREAM_TOKEN)
    # 透传 anthropic-version 头（部分 provider 需要）
    av = request.headers.get("anthropic-version")
    if av:
        headers["anthropic-version"] = av

    # 4. 日志
    model_in = body.get("model", "")
    model_out = out_body.get("model", "")
    upstream_host = ROUTER_UPSTREAM_URL.replace("https://", "").replace("http://", "").split("/")[0]
    msg = f"🟣 PROTOCOL model={model_in!r}"
    if model_out and model_out != model_in:
        msg += f" → {model_out!r}"
    msg += f" → {upstream_host}"
    logger.info(msg)

    # 5. 上游 URL
    upstream_url = f"{ROUTER_UPSTREAM_URL}{ADAPTER.upstream_path()}"
    is_stream = body.get("stream", False)

    if is_stream:
        # 流式：用 adapter 的 StreamState
        state = ADAPTER.new_stream_state(model_in)

        async def event_generator():
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=300, write=10, pool=10)) as client:
                    async with client.stream("POST", upstream_url, headers=headers, json=out_body) as resp:
                        if resp.status_code != 200:
                            # 错误：读完 body 然后包成 Anthropic error 流
                            err_content = await resp.aread()
                            logger.error(f"upstream stream error {resp.status_code}: {err_content[:300]}")
                            err_event = AnthropicEvent("error", {
                                "type": "error",
                                "error": {
                                    "type": "api_error",
                                    "message": f"upstream {resp.status_code}: {err_content.decode('utf-8', errors='replace')[:500]}",
                                },
                            })
                            yield err_event.to_sse()
                            return
                        # 正常流：每次 aiter_bytes 取一段喂给 state，吐出 Anthropic 事件
                        async for chunk in resp.aiter_bytes():
                            for ev in state.feed(chunk):
                                yield ev.to_sse()
                        # 流结束：flush
                        for ev in state.flush():
                            yield ev.to_sse()
            except Exception as e:
                logger.error(f"protocol stream error: {e}")
                err_event = AnthropicEvent("error", {
                    "type": "error",
                    "error": {"type": "api_error", "message": f"router stream error: {e}"},
                })
                yield err_event.to_sse()

        return StreamingResponse(event_generator(), media_type="text/event-stream", status_code=200)
    else:
        # 非流式
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=300, write=10, pool=10)) as client:
                resp = await client.post(upstream_url, headers=headers, json=out_body)
        except Exception as e:
            logger.error(f"protocol request error: {e}")
            raise HTTPException(status_code=502, detail=str(e))

        if resp.status_code != 200:
            return await _dump_and_respond_error(resp.status_code, resp.content, out_body)

        try:
            upstream_json = resp.json()
            anthropic_body = ADAPTER.decode_response(upstream_json)
        except Exception as e:
            logger.error(f"decode_response 失败: {e}")
            raise HTTPException(status_code=502, detail=f"decode error: {e}")

        return JSONResponse(status_code=200, content=anthropic_body)


# 延迟 import 避免循环
from protocols.base import AnthropicEvent  # noqa: E402


@app.post("/v1/messages")
async def messages(request: Request):
    if MODE == "protocol":
        return await protocol_handler(request)
    return await team_handler(request)


@app.post("/v1/messages/count_tokens")
async def proxy_count_tokens(request: Request):
    if MODE == "team":
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="invalid json body")
        model = body.get("model", "")
        route = resolve_route(request, model)
        out_body = prepare_body(body, route)
        headers = build_headers(route, request)
        upstream_url = f"{route['url']}/v1/messages/count_tokens"
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(upstream_url, headers=headers, json=out_body)
            return JSONResponse(status_code=resp.status_code, content=resp.json())
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
    # protocol 模式不直接支持 count_tokens，返回粗略估算
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json body")
    # Anthropic 协议 count_tokens 响应格式
    return JSONResponse(status_code=200, content={"input_tokens": 0})
