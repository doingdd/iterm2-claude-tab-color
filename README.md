# BurnKit

BurnKit is the directory for three standalone tools built for parallel AI coding workflows. The implementations, releases, issues, and install lifecycle now live entirely in their own repositories.

| Project | What it does | Install |
|---|---|---|
| [Claude Lanes](https://github.com/hanzhangzzz/claude-lanes) | Pins each Claude Code window to an explicit provider or team route | `npm install -g claude-lanes` |
| [Coding Usage Bar](https://github.com/hanzhangzzz/coding-usage-bar) | Shows coding-plan usage and burn pace in the macOS menu bar | `npx coding-usage-bar install` |
| [iTerm2 AI Tab Color](https://github.com/hanzhangzzz/iterm2-ai-tab-color) | Colors Claude Code and Codex tabs by attention state | Clone the repository and run `./install.sh` |

## BurnKit CLI

The remaining CLI is intentionally read-only:

```bash
npm install -g burnkit
burnkit projects
burnkit doctor
```

From a repository clone, the same commands are available as `bin/burnkit projects` and `bin/burnkit doctor`.

BurnKit no longer bundles, installs, uninstalls, or proxies the three tools. Use each standalone project's own commands and documentation.

中文说明见 [README.zh-CN.md](README.zh-CN.md)。

## License

BurnKit's own code and documentation are released under the [MIT License](LICENSE). Each standalone project defines its own source and asset licensing terms.
