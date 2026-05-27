# Claude Code Instructions

请直接遵循仓库根目录 `AGENTS.md` 的全文内容。

本文件只作为 Claude Code 的入口提示存在，不单独维护另一套项目规则，避免 `AGENTS.md` 与 `CLAUDE.md` 发生漂移。

## burn-ai 发布流程

修改 burn-ai 代码后必须执行：
1. `cd tools/burn-ai && npm version <version+1>`
2. `npm publish`
3. 提交版本变更并推送
