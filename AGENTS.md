# BurnKit 维护说明

BurnKit 是 Claude Lanes、Coding Usage Bar 和 iTerm2 AI Tab Color 的只读项目导航仓库，不再承载三个工具的实现、安装器或发布流程。

## 仓库边界

- `bin/burnkit` 只能提供 `projects`、`doctor` 和帮助信息，不得安装、卸载、代理或修改三个独立工具。
- `README.md` 与 `README.zh-CN.md` 只维护项目定位、仓库链接和独立安装入口。
- `assets/` 只保留已经渲染的 BurnKit 历史图片、动图和视频，不进入 npm 包，也不是当前宣传素材或三个独立项目的事实源。
- 三个工具的功能、文档、Issue、Release 和用户数据迁移分别在其独立仓库维护。
- Provider 图标由 Coding Usage Bar 维护；从 BurnKit 迁移时只能改变文件名或引用，不能改变图标内容。

## 独立项目

| 项目 | 仓库 | 正式安装入口 |
|---|---|---|
| Claude Lanes | `https://github.com/hanzhangzzz/claude-lanes` | `npm install -g claude-lanes` |
| Coding Usage Bar | `https://github.com/hanzhangzzz/coding-usage-bar` | `npx coding-usage-bar install` |
| iTerm2 AI Tab Color | `https://github.com/hanzhangzzz/iterm2-ai-tab-color` | clone 后执行 `./install.sh` |

## 验证

修改发布入口后至少运行：

```bash
bash -n bin/burnkit scripts/e2e-install-verify.sh
bin/burnkit --help
bin/burnkit projects
bin/burnkit doctor
scripts/e2e-install-verify.sh
npm pack --dry-run
git diff --check
```
