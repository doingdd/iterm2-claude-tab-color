# BurnKit

BurnKit 现在是三个独立工具的项目导航页。它们面向并行 AI 编程工作流，但实现、发布、Issue 和安装生命周期均由各自仓库独立维护。

| 项目 | 用途 | 安装 |
|---|---|---|
| [Claude Lanes](https://github.com/hanzhangzzz/claude-lanes) | 把每个 Claude Code 窗口明确固定到指定 Provider 或 Team 路由 | `npm install -g claude-lanes` |
| [Coding Usage Bar](https://github.com/hanzhangzzz/coding-usage-bar) | 在 macOS 菜单栏显示 Coding Plan 用量和燃烧节奏 | `npx coding-usage-bar install` |
| [iTerm2 AI Tab Color](https://github.com/hanzhangzzz/iterm2-ai-tab-color) | 按待处理状态为 Claude Code / Codex 的 iTerm2 tab 着色 | clone 仓库后执行 `./install.sh` |

## BurnKit CLI

保留的 CLI 只做只读导航和本机诊断：

```bash
npm install -g burnkit
burnkit projects
burnkit doctor
```

从仓库 clone 运行时，也可以使用 `bin/burnkit projects` 和 `bin/burnkit doctor`。

BurnKit 不再打包、安装、卸载或代理这三个工具。请使用各独立项目自己的命令和文档。

## License

BurnKit 自身代码与文档使用 [MIT License](LICENSE)。三个独立项目分别声明自己的源码与素材授权边界。
