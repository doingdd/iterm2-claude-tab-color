# Burn AI 创建计划

## Summary

- 新增第三工具 `tools/burn-ai/`，定位为 Claude Code / Codex coding plan 的本机燃烧仪表盘；截至 2026-05-08，v1 已走通 Codex 采集、launchd 持久状态、SwiftBar Menu Bar 展示和重复安装验证。
- v1 只承诺 macOS 完整可用，Windows 做通知和调度接口设计，后续补实现。
- 分发方式采用 npm CLI：用户入口为 `npx burn-ai install`，日常命令为 `burn-ai doctor/status`，Menu Bar 展示由 SwiftBar 插件承载。
- 不处理登录态、不托管凭据、不主动请求内部 usage API；只读取本机 Claude/Codex 已产生的 usage 结果。

## Key Changes

- 使用 Node/TypeScript 实现 CLI 包，通过 `package.json bin` 暴露 `burn-ai`。
- 当前阶段优先目标是走通真实数据采集与持久化：本机 Codex 实际环境作为首要验证对象，确保 usage 能稳定写入 `~/.burn-ai/`。
- CLI 命令：
  - `install`：安装 macOS launchd daemon；处理 Claude status line 接入；创建本地状态目录。
  - `uninstall`：移除本工具创建的 launchd 配置和本工具创建的 Claude 最小 status line，不改用户自有脚本。
  - `doctor`：检查 Codex/Claude 数据源、通知后端、daemon 安装状态。
  - `status`：从 `~/.burn-ai/status.json` 输出 Claude/Codex 的 5h、7d usage、reset 时间、当前燃烧状态。
  - `status --json`：输出持久化状态，供后续 Menu Bar app、脚本、iTerm badge 等展示层读取。
  - `status --refresh`：手动触发一次本机 usage 采集并刷新 `status.json`。
  - `menubar render`：输出 SwiftBar 兼容菜单文本，只读 `status.json`。
  - `menubar install`：安装 SwiftBar wrapper 插件到用户插件目录。
  - `watch`：后续可作为临时持久视图，但不是最终主展示层。
  - `ingest claude-statusline`：从 Claude Code status line stdin 读取 usage JSON，写入 `~/.burn-ai/claude/latest.json`。
- 重复执行 `burn-ai install` 必须复制最新构建产物到 `~/.burn-ai/app/`，并重启 launchd agent，确保后台进程使用最新代码。
- 已安装后的 `burn-ai install` 从 `~/.burn-ai/app` 自身运行，不能删除正在运行的 runtime；这种路径应跳过 runtime 自复制，但仍刷新 SwiftBar 插件、CLI shim 和 launchd。
- `burn-ai install` 必须创建用户级 CLI shim：`~/.local/bin/burn-ai -> ~/.burn-ai/app/dist/cli.js`，确保日常命令不依赖全局 npm install。
- `burn-ai install` 必须检查 SwiftBar；macOS 上缺失时通过 Homebrew cask 安装 SwiftBar，然后安装/更新 Burn AI SwiftBar 插件并启动 SwiftBar。
- SwiftBar 插件目录必须读取 SwiftBar 当前 `PluginDirectory`，不能假设默认插件目录生效。
- provider 监控范围由 `~/.burn-ai/config.json` 的 `providers` 控制，默认 `["codex", "claude", "glm"]`；未启用的 provider 不采集、不报缺失。
- daemon 每次运行都必须写统一状态文件 `~/.burn-ai/status.json`，该文件是后续所有展示层的唯一稳定数据入口。
- Codex 数据源：
  - 读取 `~/.codex` session/rollout JSONL 中最新 `payload.rate_limits`。
  - 使用 `primary` 作为 5h 窗口，要求 `window_minutes == 300`。
  - 使用 `secondary` 作为 7d 窗口，要求 `window_minutes == 10080`。
  - 缺字段直接报错并提示用户先正常运行 Codex CLI/App。
- Claude 数据源：
  - 如果用户没有 `statusLine.command`，`install` 创建本工具的最小 status line。
  - 如果用户已有 `statusLine.command`，安装器交互式请求确认，确认后写入 Burn AI wrapper、保存原命令元数据、更新 Claude settings；用户拒绝或非交互环境跳过修改并输出手动接入步骤。
  - 已接入时避免重复提示或重复追加。
- 通知机制：
  - 系统通知只作为唤醒信号，不作为主展示层；短暂横幅无法承载 Burn AI 的完整决策信息。
  - macOS 检测到 `terminal-notifier` 时优先使用，并通过 `-contentImage` 展示运行时生成的动态数据卡片。
  - `terminal-notifier` 不可用时 fallback 到 `osascript display notification`。
  - Windows 设计为 `BurntToast` 后端，`doctor` 给出 `Install-Module BurntToast -Scope CurrentUser` 提示，但 v1 不承诺实现。
  - iTerm2 badge/tab color 不作为 v1 默认通知，只保留未来可选增强。

## Data And Display Direction

- Burn AI 的状态是持续状态，不是一条短事件通知。
- 完整信息必须落在持久数据层：`~/.burn-ai/status.json`。
- CLI、watch、未来 Menu Bar app、未来 iTerm badge 都只能读取 `status.json`，不能各自重新实现采集逻辑。
- `burn-ai status` 默认读取 `status.json`，但会基于当前时间重新计算数据年龄和 stale 标记；只有 `--refresh`、`--fixtures` 或 daemon 触发 provider 文件采集。
- 当前阶段先完成：
  - Codex 真实环境数据采集。
  - Claude ingest cache 格式。
  - `status.json` 结构和写入时机。
  - `burn-ai status --json` 可读回同一份状态。
- 展示层后续方向：
  - v1 先用 SwiftBar 插件走通 macOS Menu Bar 体验链条。
  - SwiftBar 标题栏必须保持单个稳定 title item；不要用 `---` 前多行 header 表达多个 provider，否则 SwiftBar 会把它们当成多个菜单栏项轮流展示。
  - 如果标题栏需要每个 provider 的独立图标，使用运行时生成的单张透明 PNG 传给 SwiftBar `image=`，不要拆成多个 title item。
  - 长期首选原生 macOS Menu Bar app，作为主展示层。
  - Menu Bar 展示层只读 `status.json`，不参与采集、不处理登录、不访问 provider 文件。
  - 系统通知文案后续应收敛为“需要注意，去看状态面板”，而不是试图在横幅里讲完整策略。

## Burn Strategy

- 只提供两档：
  - `low`：默认档，相当于 balanced。
  - `high`：激进档，相当于 aggressive。
- 核心目标不是打满每个 5h，而是在 7d 总预算约束下让 5h 不空转。
- 持续记录 usage 样本，学习转换系数：
  - `k = delta_7d_pct / delta_5h_pct`
  - 含义：消耗 1% 的 5h quota，大约推动多少 7d usage。
- 根据 7d 剩余额度和剩余 5h slot 数计算当前 5h 推荐目标：
  - `remaining_7d_pct = 100 - seven_day.used_percent`
  - `remaining_5h_slots = seven_day_remaining_minutes / 300`
  - `weekly_budget_per_5h_slot = remaining_7d_pct / remaining_5h_slots`
  - `recommended_5h_pct = weekly_budget_per_5h_slot / k`
- 默认目标区间：
  - `low`: `recommended_5h_pct * 0.8` 到 `recommended_5h_pct * 1.1`
  - `high`: `recommended_5h_pct * 0.9` 到 `recommended_5h_pct * 1.35`
- 状态分类：
  - `UNDER_BURN`：当前 5h 低于动态目标区间。
  - `ON_TRACK`：当前 5h 位于动态目标区间。
  - `OVER_BURN`：当前 5h 高于动态目标区间，按当前节奏可能提前撞 7d。
  - `LIMIT_RISK`：5h 或 7d 已接近上限。
- 冷启动时样本不足，不假装给动态建议；只展示原始 usage、reset 时间和明显 limit risk。

## Test Plan

- 单元测试：
  - Codex JSONL 解析：正常 rate_limits、缺字段、过期数据、多个 session 取最新。
  - Claude ingest：stdin JSON 解析、标准化写入、无 usage 字段时报错。
  - 动态燃烧算法：样本不足、k 计算、low/high 区间、UNDER/ON_TRACK/OVER/LIMIT_RISK 分类。
  - 通知冷却：同 provider/window/reason 不重复刷屏，窗口 reset 后清空冷却。
- 集成测试：
  - `burn-ai doctor` 在缺 Codex、缺 Claude cache、缺通知后端时输出可执行修复提示。
  - `burn-ai status` 输出 Claude/Codex 5h/7d 表格。
  - macOS `install --dry-run` 不写文件，只展示将修改的 launchd 和 Claude status line 行为。
- 验证命令：
  - `npm test`
  - `npm run build`
  - `npx --no-install burn-ai install`
  - `burn-ai install`
  - `npx burn-ai doctor --dry-run`
  - `npx burn-ai status --fixtures`
  - `npx burn-ai menubar render`

## Assumptions

- 包名和命令名采用 `burn-ai`，目标入口为 `npx burn-ai install`。
- v1 先服务 macOS 用户，Windows 只做设计和 doctor 提示。
- 用户已经订阅并登录 Claude Code / Codex；本工具不负责登录和凭据恢复。
- Claude Code 已有 status line 属于用户资产，本工具不自动覆盖、不透明代理。
- `tools/burn-ai/plan.md` 是第三工具的阶段性产品与技术基准文档。
