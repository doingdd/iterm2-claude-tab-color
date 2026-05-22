# BurnKit

BurnKit 是面向高并发 AI 编程工作流的三件套工具集，不再按单一 iTerm2 tab color 项目维护。

## 项目结构基准

| 路径 | 职责 |
|------|------|
| `bin/burnkit` | 对用户可见的统一入口：doctor、安装/卸载编排、router/burn-ai 命令转发 |
| `tools/claude-provider-router/` | 第一件工具：`c` 启动器、Provider 配置、Team 路由代理、Claude Code status line |
| `tools/iterm2-tab-color/` | 第二件工具：Claude Code / Codex CLI idle hook、iTerm2 tab color daemon |
| `tools/burn-ai/` | 第三件工具：Claude Code / Codex coding plan usage 采集、燃烧策略、系统通知 |
| `assets/` | 演示图、发布素材 |
| `raw_think.md` | 本地想法草稿，不作为产品承诺 |

根目录不保留 `install.sh` / `uninstall.sh` 兼容入口。对用户可见的安装、卸载统一走 `bin/burnkit install ...` / `bin/burnkit uninstall ...`。子工具目录中的 `install.sh` / `uninstall.sh` 只能作为兼容 wrapper 或内部实现入口，不应再作为文档主路径。

## 目录迁移基准

- 目录迁移只做目录整理，不做功能层面的代码修改。
- `tools/iterm2-tab-color/` 的行为基准是当前 HEAD `0ba4914 feat: improve codex install and idle cleanup` 下的根目录实现。
- 允许改动的代码范围仅限路径适配：脚本相对路径、launchd plist 生成路径、hook 安装目标、README 中的命令路径。
- 禁止在目录迁移中引入新的 state 清理策略、颜色聚合策略、进程检测逻辑、hook 事件语义或 daemon 调度逻辑。
- 如果必须修改功能代码，必须暂停迁移，单独提出 bugfix 计划并先 review。

## 维护原则

- `tools/burn-ai/` 不处理登录态、不托管凭据、不主动请求内部 usage API；只读取本机 Claude/Codex 已产生的 usage 结果。
- `tools/burn-ai/` 的默认分发入口是 `npx burn-ai install`，日常命令是 `burn-ai doctor/status`。
- `bin/burnkit install burn` 可以在本地 clone 场景执行 `npm install` / `npm run build` 后转发到 `npx --no-install burn-ai install`，但不能改变 Burn AI 自身安装语义。
- `tools/burn-ai/` 的 v1 展示层是 SwiftBar 菜单栏插件。安装器必须优先读取 SwiftBar 当前 `PluginDirectory`，把 `burn-ai.1m.js` 写到用户实际插件目录；不能假设默认目录一定生效。
- `tools/burn-ai/` 安装器必须支持两条真实路径：从 `npx --no-install burn-ai install` 更新 `~/.burn-ai/app`，以及从已安装 shim 直接执行 `burn-ai install` 时不能删除正在运行的 runtime，只刷新插件、CLI shim 和 launchd。
- Claude Code 已有 status line 属于用户资产；`tools/burn-ai/` 不能覆盖用户脚本。若用户已有 `statusLine.command` 且未接入 Burn AI ingest，安装器必须交互式说明将写入 Burn AI wrapper、保存原命令元数据并更新 Claude settings；用户确认后才可接入，拒绝或非交互环境必须跳过并提示手动接入步骤与功能影响。
- `tools/claude-provider-router/config.env` 是敏感本地配置，不能提交；只维护 `config.env.example`。
- `tools/claude-provider-router/install-core.sh` 是 router 安装内部实现；`bin/burnkit install router` 必须直接调用它。该安装器只能在缺少 `config.env` 时从模板创建并 `chmod 600`，重复安装必须 byte-for-byte 保留已有 `config.env` 和权限。
- `tools/iterm2-tab-color/install-core.sh` 是 tab color 安装内部实现；`bin/burnkit install tabs` 必须直接调用它。`tools/iterm2-tab-color/install.sh` 只保留兼容提示和转发。
- `tools/iterm2-tab-color/uninstall-core.sh` 必须与 install 行为对称：停止 launchd、删除 hook 文件和 launchd plist、清理 JSON hook 条目，并默认保留 state/log。`tools/iterm2-tab-color/uninstall.sh` 只保留兼容提示和转发。
- `tools/iterm2-tab-color/` 的 LaunchAgent plist 必须安装为 `~/Library/LaunchAgents/com.duying.tab-color-daemon.plist` 真实文件，不能依赖指向仓库的软链。
- `tools/iterm2-tab-color/tab_color_daemon.py` 必须启用 iTerm2 Python API retry；iTerm2 重启或升级导致 websocket 断开时，daemon 应自动重连。
- 修改脚本后至少运行 `bash -n`、`py_compile` 和 tab color 单元测试。

## 验证命令

修改发布入口后至少运行：

```bash
bash -n bin/burnkit
bin/burnkit --help
bin/burnkit doctor
scripts/e2e-install-verify.sh --dry-run
```

`scripts/e2e-install-verify.sh` 必须包含并通过 router `config.env` 双路径测试：裸环境缺少 `config.env` 时必须从模板创建并设为 `600`；当 `config.env` 已存在时，`bin/burnkit install router` 不能修改其内容或权限。真实安装验证必须由用户确认后执行 `scripts/e2e-install-verify.sh --real`。

目录迁移完成后至少运行：

```bash
bash -n tools/iterm2-tab-color/install-core.sh tools/iterm2-tab-color/uninstall-core.sh tools/iterm2-tab-color/install.sh tools/iterm2-tab-color/uninstall.sh tools/iterm2-tab-color/tab_color_hook.sh
python3 -m py_compile tools/iterm2-tab-color/tab_color_daemon.py tools/iterm2-tab-color/reset_tab.py tools/iterm2-tab-color/test_daemon.py
python3 -m unittest tools/iterm2-tab-color/test_daemon.py
```

如果修改 `tools/claude-provider-router/`，还要对该目录的 shell/Python 文件执行对应语法检查。

如果修改 `tools/burn-ai/`，至少运行：

```bash
cd tools/burn-ai
npm ci
npm test
npm run build
npx --no-install burn-ai install
burn-ai install
npx --no-install burn-ai doctor --dry-run
npx --no-install burn-ai status --fixtures
npx --no-install burn-ai menubar render
```

`npx --no-install burn-ai install` 是真实安装验证，必须确认它会复制最新代码并重启 launchd agent；随后还必须跑一次 `burn-ai install`，确认已安装入口重复安装不会自删 runtime，且 SwiftBar 插件仍能渲染。

## npm 发布

`tools/burn-ai/` 已发布为 npm 包 `burn-ai`，用户入口是 `npx burn-ai install`。每次 commit 涉及 `tools/burn-ai/` 下的代码变更时，必须在 commit 之后、push 之前执行：

1. `cd tools/burn-ai && npm version patch`（feat 级别变更用 `minor`）
2. `npm publish`
3. 将 `package.json` 和 `package-lock.json` 的版本变更纳入同一 commit（通过 amend 或新 commit）

这确保用户通过 `npx` 拿到的始终是最新版本。
