# BurnKit — Claude Provider Router + iTerm2 Tab Color + Burn AI

> 先榨干你，再逼你 harness。

[English README](README.md)

三件套工具给并行跑 Claude Code 和 Codex 的开发者：路由到正确 Provider、空闲 tab 染色提醒、盯住昂贵 plan 窗口。

中文精神名：`卷王三件套`。

不是卷疯了，也不是想把你卷疯。

BurnKit 把 AI 编程的尴尬事实摊开：模型越来越快，但工作流仍卡在人身上。拖慢吞吐的是 Provider 选择、空闲 session、上下文切换、plan 窗口浪费，以及那个永远在回答"要不要继续"的人类调度器。

BurnKit 先把这些变成信号。等信号多到接不住，你会自然开始问：怎么让 AI 少问我？怎么让它自己排队、分工、验证、交付？

没错，这就是 harness 的入口。

## 你会得到什么

### Claude Provider Router — `c` 命令

不用改配置就切换 Provider。Agent Team 可分流到不同 Provider。

```
c 0              # 使用 Provider 0（如 Claude）
c team 2 0       # Team 模式：leader 用 2，teammate 用 0
```

<img src="assets/launch/c-router-launch.gif" alt="Claude Provider Router 用 c 0 和 c team 2 0 展示模型切换" width="320">

### iTerm2 Tab Color — 空闲 tab 信号

AI 等你时 tab 变绿/黄/红。只有非当前 tab 变色——提示指向你没看的东西。

```
burnkit install tabs
```

| 颜色 | 含义 |
|------|------|
| 绿色 | AI 刚跑完，现在收结果 |
| 黄色 | 等了一会儿，并行能力漏水 |
| 红色 | 等太久了，人迟到了 |
| 白色 | 当前 tab、处理中、干净 |

<img src="assets/demo-tab-color.gif" alt="iTerm2 tab 用绿色、黄色、红色展示等待压力" width="320">

### Burn AI — Plan 燃烧追踪

菜单栏展示 5h/7d 用量，彩色进度条。不登录，只读本机 Claude Code / Codex 数据。

```
burnkit status --refresh
```

<img src="assets/menubar-screenshot-basic.png" alt="Burn AI 菜单栏展示 5 个 Provider 图标和用量条" width="320">

> **提示：** 菜单栏拥挤？用 [Dozer](https://github.com/Mortennn/Dozer) 隐藏图标，让 Burn AI 更突出。

## 安装

```bash
npm install -g burnkit
burnkit install all
```

或不用安装直接运行：

```bash
npx burnkit install all
```

![burnkit install all --dry-run](assets/demo-install.gif)

编辑 Provider 配置（添加 token）：

```bash
# Claude / Codex 路由
$EDITOR ~/.burnkit-router/config.env

# DeepSeek / GLM / MiniMax 用量追踪
$EDITOR ~/.burn-ai/config.json
```

`~/.burn-ai/config.json` 示例：

```json
{
  "providers": ["codex", "claude", "deepseek", "glm", "minimax"],
  "deepseek": {
    "apiKey": "your-deepseek-api-key"
  },
  "glm": {
    "baseUrl": "https://open.bigmodel.cn",
    "apiKey": "your-zhipu-api-key"
  },
  "minimax": {
    "region": "cn",
    "apiKey": "your-minimax-api-key"
  }
}
```

用 Provider 启动 Claude Code：

```bash
c 0
c team 2 0
```

检查 plan 燃烧状态：

```bash
burnkit status --refresh
```

卸载：

```bash
burnkit uninstall all
```

## 让你的 Agent 自动安装

```text
帮我在这个仓库里安装 BurnKit。先 `burnkit install all --dry-run` 看改动，等我确认后再真实安装。~/.burnkit-router/config.env 如果存在要保留。
```

## 它制造的循环

开更多 AI session → 看 tab 变绿/黄/红 → 看 Burn AI 浪费 5h/7d 窗口 → 撞人类调度极限 → 建真正的 agent harness。

BurnKit 是压力装置。暴露隐藏成本，让问题从鸡血口号变成架构问题。

## 命令

| 命令 | 用途 |
|------|------|
| `burnkit doctor` | 检查依赖和工具就绪状态 |
| `burnkit install all` | 安装 router、tabs、burn-ai |
| `burnkit uninstall all` | 卸载所有工具 |
| `c 0` / `c 1` / `c 2` | 用 Provider N 启动 Claude Code |
| `c team 2 0` | Team 模式：leader 用 2，teammate 用 0 |
| `burnkit status --refresh` | 检查 plan 燃烧 |

## 项目结构

```
bin/burnkit                          # CLI 入口
tools/claude-provider-router/        # c 命令、config.env
tools/iterm2-tab-color/              # Python daemon + shell hooks
tools/burn-ai/                       # Node.js CLI + SwiftBar 插件
```

## 工具文档

- [Claude Provider Router](tools/claude-provider-router/README.md)
- [iTerm2 Tab Color](tools/iterm2-tab-color/README.md) · [中文](tools/iterm2-tab-color/README.zh-CN.md)
- [Burn AI](tools/burn-ai/README.md)

## 安全边界

- `~/.burnkit-router/config.env` 包含 token，不能提交。
- Burn AI 不处理登录态、不托管凭据、不主动请求内部 usage API；只读 Claude Code / Codex 已在本机产生的数据。
- Burn AI 不覆盖用户已有 Claude Code status line。若已存在，会先交互式确认再安装 wrapper。
- tab 颜色行为、state 清理、进程检测、hook 事件、daemon 调度属功能行为变更，不混进文档或发布润色。

## 开发验证

发布入口修改后：

```bash
bash -n bin/burnkit
bin/burnkit --help
bin/burnkit doctor
scripts/e2e-install-verify.sh --dry-run
```

本机真实安装验证：

```bash
scripts/e2e-install-verify.sh --real
```

iTerm2 Tab Color 修改后：

```bash
bash -n tools/iterm2-tab-color/install-core.sh tools/iterm2-tab-color/uninstall-core.sh
python3 -m py_compile tools/iterm2-tab-color/tab_color_daemon.py tools/iterm2-tab-color/reset_tab.py
python3 -m unittest tools/iterm2-tab-color/test_daemon.py
```

Burn AI 修改后：

```bash
cd tools/burn-ai && npm ci && npm test && npm run build
npx --no-install burn-ai doctor --dry-run
npx --no-install burn-ai status --fixtures
git diff --check
```

## License

[MIT](LICENSE)