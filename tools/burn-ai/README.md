# Burn AI

Burn AI is BurnKit's plan-burn layer. It monitors local Claude Code, Codex, GLM (Zhipu AI), DeepSeek, and MiniMax coding plan usage, then tells you whether your current pace is under-burning, on track, over-burning, or close to a limit.

It does not manage login state or credentials. For Claude and Codex it reads usage data already produced locally. For GLM, DeepSeek, and MiniMax it calls the respective quota/balance API using an API key stored in `~/.burn-ai/config.json`.

## Quick Start

From the BurnKit repo root:

```bash
bin/burnkit install burn
bin/burnkit status --refresh
```

Direct package flow:

```bash
npx burn-ai install
burn-ai doctor
burn-ai status
```

`install` creates a local runtime at `~/.burn-ai/app`, a user-level CLI shim at `~/.local/bin/burn-ai`, a launchd agent, a default config file, and a SwiftBar menu bar plugin. Make sure `~/.local/bin` is in your `PATH`.

## Commands

| Command | Purpose |
|---------|---------|
| `burn-ai install` | Install runtime, launchd checker, SwiftBar host/plugin, and Claude ingest when safe |
| `burn-ai uninstall` | Remove Burn AI managed launchd/status line/plugin config |
| `burn-ai doctor` | Check local Codex/Claude/GLM usage sources and notification backend |
| `burn-ai status` | Print 5h/7d usage and burn state from `~/.burn-ai/status.json` |
| `burn-ai status --json` | Print the same status snapshot written to `~/.burn-ai/status.json` |
| `burn-ai status --refresh` | Re-collect local usage before printing status |
| `burn-ai menubar render` | Print SwiftBar-compatible menu text from `~/.burn-ai/status.json` |
| `burn-ai menubar install` | Install the SwiftBar plugin wrapper |
| `burn-ai menubar uninstall` | Remove the Burn AI managed SwiftBar plugin |
| `burn-ai ingest claude-statusline` | Read Claude Code status line JSON from stdin and cache usage |

## Install Behavior

`burn-ai install` is designed to be repeatable.

- From `npx burn-ai install` or `npx --no-install burn-ai install`, it copies the current package into `~/.burn-ai/app` through a temporary directory, then restarts launchd.
- From a BurnKit source checkout, `bin/burnkit install burn` and `bin/burnkit install all` rebuild `tools/burn-ai` before running the package installer, so local TypeScript changes do not leave an old `dist/` in the installed runtime.
- From the installed shim `burn-ai install`, it detects that it is already running from `~/.burn-ai/app`, skips runtime self-copy, and still refreshes the CLI shim, SwiftBar plugin, and launchd agent.
- The launchd job runs `~/.burn-ai/app/dist/cli.js daemon --once` every 300 seconds.
- The installer does not overwrite user-managed Claude Code status line scripts. If one already exists, it asks before installing a Burn AI wrapper around it.

## Claude Code Status Line

If you do not have a Claude Code status line, `burn-ai install` can create a minimal one.

If you already have one, Burn AI will ask before changing Claude settings. When you answer `y`, it writes a Burn AI wrapper at `~/.burn-ai/claude/statusline.sh`, saves the original command metadata, and updates `statusLine.command` so the wrapper runs first. The wrapper ingests Claude usage, then forwards the same input to your existing status line command. `burn-ai uninstall` restores the original command.

If you answer `n` or run in a non-interactive shell, Burn AI skips the status line update and prints manual setup instructions. Add this near the top of your own script:

```bash
input="$(cat)"
printf "%s" "$input" | node "$HOME/.burn-ai/app/dist/cli.js" ingest claude-statusline >/dev/null

# Make the rest of your script read from "$input" instead of stdin.
```

Without this integration, Claude usage stays unavailable in Burn AI. Claude burn-rate analysis, Claude notifications, and Claude menu bar data will report `CLAUDE_INGEST_MISSING`; Codex usage is unaffected.

## Codex

Burn AI reads Codex `payload.rate_limits` from local `~/.codex` JSONL session data. If no such data exists, run Codex CLI or Codex App once and complete a normal interaction.

## GLM (Zhipu AI)

Burn AI calls the Zhipu AI quota API (`GET /api/monitor/usage/quota/limit`) to read 5h and 7d usage windows. You need to set `glm.apiKey` in `~/.burn-ai/config.json`:

```json
{
  "providers": ["codex", "claude", "glm"],
  "glm": {
    "baseUrl": "https://open.bigmodel.cn",
    "apiKey": "your-api-key"
  }
}
```

Get your API key from the [Zhipu AI console](https://open.bigmodel.cn). Without this key, GLM monitoring will report `GLM_API_KEY_MISSING`.

## DeepSeek

Burn AI calls `GET https://api.deepseek.com/user/balance` to read your account balance. DeepSeek exposes balance rather than 5h/7d usage windows, so the menu bar shows `Available` or `Depleted` plus the currency amount. Set `deepseek.apiKey` in `~/.burn-ai/config.json`:

```json
{
  "providers": ["codex", "claude", "glm", "deepseek"],
  "deepseek": {
    "apiKey": "your-deepseek-api-key"
  }
}
```

Without this key, DeepSeek monitoring will report `DEEPSEEK_API_KEY_MISSING`.

## MiniMax (M3)

Burn AI calls `GET https://api.minimaxi.com/v1/token_plan/remains` to read 5h and 7d usage windows. MiniMax returns two signals per model: a count-based ratio (`current_*_usage_count` / `current_*_total_count`) for tiered models like `video`, and a credit-based `current_*_remaining_percent` (0-100) for the `general` model where the count stays at 0. Burn AI prefers the count ratio when total > 0 and otherwise derives used% as `100 - remaining_percent`. Set `minimax.apiKey` and (optionally) `minimax.region` in `~/.burn-ai/config.json`:

```json
{
  "providers": ["codex", "claude", "glm", "minimax"],
  "minimax": {
    "region": "cn",
    "apiKey": "your-minimax-api-key"
  }
}
```

`region` defaults to `cn` (api.minimaxi.com); set it to `global` to use api.minimax.io. Without this key, MiniMax monitoring will report `MINIMAX_API_KEY_MISSING`.

## Profiles

Set `BURN_AI_PROFILE=high` for the more aggressive profile. The default is `low`.

```bash
BURN_AI_PROFILE=high burn-ai status
```

Both profiles are constrained by the 7d budget. Burn AI does not treat "fill every 5h window" as the goal.

## Provider Config

`burn-ai install` creates `~/.burn-ai/config.json`:

```json
{
  "providers": ["codex", "claude", "glm"]
}
```

Remove a provider from this list if you do not want Burn AI to monitor it. For one-off runs, `BURN_AI_PROVIDERS=codex burn-ai status --refresh` also works.

## Menu Bar

The first menu bar implementation uses SwiftBar as a thin host. Burn AI still owns collection and state; the SwiftBar plugin only runs `burn-ai menubar render` and reads `~/.burn-ai/status.json`.

`burn-ai install` checks for SwiftBar and installs it with Homebrew cask when it is missing. It installs the Burn AI plugin into SwiftBar's configured `PluginDirectory`, not blindly into a hardcoded default directory, then opens SwiftBar.

```bash
burn-ai menubar install
```

If SwiftBar is not installed, `burn-ai doctor` will report it. If SwiftBar already has a custom plugin folder, Burn AI uses that folder.

The menu bar title shows the official Codex, Claude Code, Zhipu AI, DeepSeek, and MiniMax icons with compact 5h/7d percentages (DeepSeek shows balance instead of windows):

```text
{Codex icon} 5H:14%,7D:67% │ {Claude icon} 5H:24%,7D:74% │ {GLM icon} 5H:36%,7D:7%
```

SwiftBar only supports one bitmap image on a single stable title item, so Burn AI renders the full title into one transparent PNG at render time. That bitmap keeps each provider icon next to its own usage segment while avoiding SwiftBar's multi-title rotation behavior. The dropdown stays read-only and uses SwiftBar-native symbols, badges, progress meters, reset time, target range, data age, and warnings. SwiftBar is a host dependency; `burn-ai uninstall` removes the Burn AI plugin but does not uninstall SwiftBar itself.

## Runtime Files

| Path | Purpose |
|------|---------|
| `~/.burn-ai/app/` | Stable runtime copy used by launchd, Claude ingest hints, and SwiftBar |
| `~/.burn-ai/config.json` | Provider selection, default `["codex", "claude", "glm"]` |
| `~/.burn-ai/status.json` | Stable display-layer entry point |
| `~/.burn-ai/codex/latest.json` | Latest normalized Codex usage |
| `~/.burn-ai/claude/latest.json` | Latest normalized Claude usage after status line ingest |
| `~/.burn-ai/glm/latest.json` | Latest normalized GLM usage from Zhipu AI quota API |
| `~/.burn-ai/deepseek/latest.json` | Latest normalized DeepSeek balance from DeepSeek API |
| `~/.burn-ai/minimax/latest.json` | Latest normalized MiniMax 5h/7d usage from MiniMax API |
| `~/.local/bin/burn-ai` | CLI shim pointing at the stable runtime |
| `~/Library/LaunchAgents/com.duying.burn-ai.plist` | macOS launchd agent |
| SwiftBar `PluginDirectory` / `burn-ai.1m.js` | Menu bar plugin wrapper |

## Notifications

On macOS, Burn AI uses `terminal-notifier` when available and attaches a dynamic data card as the notification content image.

The card shows provider, 5h used percentage, state label, target range, and reset countdown. This avoids asking users to infer meaning from a red/yellow icon.

If `terminal-notifier` is unavailable, Burn AI falls back to `osascript display notification`, which uses the system default notification appearance.

See [plan.md](plan.md) for the current product and technical baseline.
