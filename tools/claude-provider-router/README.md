# Claude Provider Router

> BurnKit 第一件工具：让 Claude Code 的模型、Provider、Team 路由和状态行更可控。

这个目录整合了原有 `c` 工具。`c` 是主入口，负责按编号启动 Claude Code；Team 模式下会启动本地 FastAPI 路由代理，把 leader 和 teammate 请求分发到不同 Provider。

## 文件说明

| 文件 | 说明 |
|------|------|
| `c` | 主启动脚本，支持单 Provider 和 `c team` |
| `router.py` | Team 模式本地路由代理，按 auth token 区分 leader / teammate |
| `router-auth-helper.sh` | Claude Code `apiKeyHelper`，给单 Provider 和 Team 模式提供 token |
| `ccline-with-model.sh` | Claude Code status line，显示模型、上下文、耗时、Git 分支和高级能力调用次数 |
| `install-core.sh` | 内部安装器；由 `bin/burnkit install router` 调用 |
| `config.env.example` | Provider 配置模板 |
| `config.env` | 本地真实配置，包含 token，必须忽略提交 |

## 安装准备

从仓库根目录走 BurnKit 统一入口：

```bash
bin/burnkit install router
c 0
```

`install-core.sh` 只在 `config.env` 缺失时从 `config.env.example` 创建并设置权限为 `600`。如果 `config.env` 已经存在，重复安装必须原样保留内容和权限。安装器还会把 `c` 安装为 `~/.local/bin/c` 软链；如果该路径已有用户自己的命令，会跳过不覆盖。

如果只使用本工具，也可以手动配置：

```bash
cd tools/claude-provider-router
cp config.env.example config.env
chmod 600 config.env
```

编辑 `config.env`，按编号填写 Provider：

```bash
CONFIG_0_BASE_URL=https://api.example.com/anthropic
CONFIG_0_AUTH_TOKEN=your-token
CONFIG_0_COMPACT_WINDOW=150000
CONFIG_0_MODEL=your-model-name
```

Team 模式需要：

```bash
python3 -m pip install fastapi uvicorn httpx
```

`ccline-with-model.sh` 需要 `jq`：

```bash
brew install jq
```

## Claude Code 设置

`c` 默认使用 `~/.claude/settings-c.json`。至少需要配置 `apiKeyHelper`：

```json
{
  "apiKeyHelper": "/absolute/path/to/tools/claude-provider-router/router-auth-helper.sh"
}
```

如果希望启用状态行，把同一个文件扩展为：

```json
{
  "apiKeyHelper": "/absolute/path/to/tools/claude-provider-router/router-auth-helper.sh",
  "statusLine": {
    "type": "command",
    "command": "/absolute/path/to/tools/claude-provider-router/ccline-with-model.sh",
    "padding": 0
  }
}
```

也可以把 `statusLine` 放到 `~/.claude/settings.json`，让普通 `claude` 启动方式也使用同一条状态行。

## 使用

从仓库根目录：

```bash
c 0
c 2 --resume
c team 2 0
c router status
```

从本目录：

```bash
# 显示帮助和可用配置
./c

# 使用配置 0 启动
./c 0

# 使用配置 2 并恢复会话
./c 2 --resume

# Team 模式：leader 用 2，teammate 用 0
./c team 2 0

# 查看或停止路由代理
./c router status
./c router stop
```

如果要手动创建全局 `c` 软链：

```bash
mkdir -p "$HOME/.local/bin"
ln -sf "$PWD/c" "$HOME/.local/bin/c"
```

## Team 模式路由

```text
Claude Code leader
  ├─ ANTHROPIC_BASE_URL=http://127.0.0.1:{port}
  └─ CLAUDE_TEAM_ROLE=leader
        |
        v
apiKeyHelper -> leader-token

Claude Code teammate
  └─ CLAUDE_TEAM_ROLE 被 Claude Code 过滤
        |
        v
apiKeyHelper -> teammate-token

router.py
  ├─ leader-token   -> leader Provider
  └─ teammate-token -> teammate Provider
```

## 安全边界

- 不要提交 `config.env`、`.routers/` 或路由失败请求体。
- `router.py` 只监听 `127.0.0.1`，不要改成公网监听。
- `router-auth-helper.sh` 只在本地路由代理场景返回占位 token；普通 Provider 模式返回 `CLAUDE_AUTH_TOKEN`。

## 协议转换模式（`CONFIG_X_PROTOCOL=openai`）

部分后端（很多 vLLM 部署、OpenAI 兼容代理）**只支持 OpenAI Chat Completions 协议**，不认 Anthropic 的 tools 格式。Claude Code 一上来就发 system prompt + 几十个 tools，直接打过去会被 400 拒掉。

设置 `PROTOCOL=openai` 后，`c` 会自动起一个本地 router 代理（端口 **4100**），把 Anthropic 请求翻译成 OpenAI 协议再转发，响应再翻译回 Anthropic 格式。多个 c 进程共享同一个 router 实例（通过 pid 文件 + mkdir 锁防双开）。

### 配置示例

```env
CONFIG_8_BASE_URL=https://your-openai-compatible-endpoint.example.com/v1
CONFIG_8_AUTH_TOKEN=sk-xxxxx
CONFIG_8_MODEL=your-model-name
CONFIG_8_PROTOCOL=openai
```

### 架构

```
$ c 8
  ↓
1. 读 config.env → CONFIG_8_PROTOCOL=openai
2. 启动/复用本地 router 代理（端口 4100）
3. ANTHROPIC_BASE_URL=http://127.0.0.1:4100
   CLAUDE_PROTOCOL=openai  (让 apiKeyHelper 透传真实 token)
4. exec claude

Claude Code
  ↓ POST /v1/messages (Anthropic 协议 + tools + stream)
router.py :4100 (openai 模式)
  ├─ OpenAIAdapter.encode_request(anthropic_body)
  │    - tools: [{name, input_schema}] → [{type:"function", function:{name, parameters:input_schema}}]
  │    - system 顶级字段 → messages[0] role=system
  │    - user/assistant/tool_use/tool_result 块双向转换
  ├─ httpx stream POST {upstream}/v1/chat/completions
  └─ OpenAIToAnthropicStreamState.feed(chunk) → Anthropic SSE
       - 文本流 + tool_calls 流（name 一次到 / arguments 分片累积）
       - finish_reason → stop_reason 映射
       - zero-copy 直接 yield 字节串
```

### 进程管理命令

```bash
c router status          # 列出所有 router（team + protocol，标注模式）
c router stop            # 停掉所有 router
c router stop team       # 只停 team 模式
c router stop openai     # 只停 openai 协议模式
```

- 端口：固定 **4100**（区别于 team 模式的 3100+ 动态段）
- pid 文件：`.routers/openai-4100.pid` + 锁目录 `.routers/openai-4100.lock`
- c 退出**不杀** router（共享）；用 `c router stop openai` 手动停

### 扩展新协议

1. 在 `protocols/` 下新建 `gemini_adapter.py`
2. 实现 `ProtocolAdapter` 接口（`encode_request` / `decode_response` / `upstream_path` / `upstream_headers` / `new_stream_state`）
3. 用 `@register_adapter` 装饰器
4. 在 `protocols/__init__.py` 加 `from . import gemini_adapter  # noqa: F401`
5. `c` 脚本 `router_acquire_or_start` 的 case 加 `gemini) port=4101 ;;`
6. config.env 加 `CONFIG_X_PROTOCOL=gemini`

router.py、`c` 脚本主逻辑、其他 adapter 都不需要改。

### 已知限制

- **图片内容（image blocks）**：MVP 暂不支持
- **Prompt caching 字段映射**：暂未做
- **Extended thinking / reasoning_effort**：暂不支持
- **协议模式 router 挂了不会自动热重连**：用户需要重新 `c`
- **协议模式暂不支持 team 并发**：team 模式 router 和 openai 模式 router 是两个独立进程
- **OpenAI usage 字段时序**：OAI 流式协议 usage 在最后一个 chunk 才到，Anthropic `message_start` 事件里的 `input_tokens` 因此是 0（Anthropic 协议允许此字段为 0/None）
