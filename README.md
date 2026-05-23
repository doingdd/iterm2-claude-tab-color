# BurnKit

> Overclock the human. Then build the harness.

[中文说明](README.zh-CN.md)

BurnKit is a three-tool kit for developers running Claude Code and Codex in parallel. It routes work to the right provider, colors idle terminal tabs when AI is waiting for you, and tracks plan burn before expensive windows quietly evaporate.

Chinese spirit name: `卷王三件套`.

This is not about going productivity-crazy, and it is not about making you go crazy either.

BurnKit exposes the awkward truth inside AI coding: models keep getting faster, but the workflow still jams around the human operator. You think you need a stronger model. Then you notice the real drag: provider choice, idle sessions, context switching, wasted plan windows, and the person who keeps answering "should I continue?"

BurnKit turns those hidden costs into signals. When the signals become too many to handle manually, the next question becomes obvious: how do you make AI ask less, queue work, split tasks, verify results, and ship without constant babysitting?

That is the harness entrance.

## What You Get

| Tool | Effect | Main command | What it does |
|------|--------|---------------|--------------|
| Claude Provider Router | <img src="assets/launch/c-router-launch.gif" alt="Claude Provider Router switches Claude Code model lanes and splits team traffic" width="320"> | `c 0`<br>`c team 2 0` | Starts Claude Code through numbered providers and routes Agent Team leader/teammate traffic. |
| iTerm2 Tab Color | <img src="assets/demo-tab-color.gif" alt="iTerm2 tab colors show green, yellow, and red idle indicators" width="320"> | `burnkit install tabs` | Colors inactive Claude Code / Codex tabs when they are waiting for you. |
| Burn AI | <img src="assets/menubar-screenshot.png" alt="SwiftBar menubar shows Codex and Claude burn pace at a glance" width="320"> | `burnkit status --refresh` | Tracks local Claude Code / Codex plan usage and burn pace. |

> **Tip:** If your menu bar gets crowded, consider a free tool like [Dozer](https://github.com/Mortennn/Dozer) to hide less-used icons and keep the Burn AI menubar signal front and center.

## Install

```bash
npm install -g burnkit
burnkit install all
```

Or run without installing:

```bash
npx burnkit install all
```

![burnkit install all --dry-run](assets/demo-install.gif)

From source:

```bash
git clone https://github.com/hanzhangzzz/burnkit.git
cd burnkit
bin/burnkit install all
```

Then edit your provider config:

```bash
$EDITOR $(dirname $(readlink -f ~/.local/bin/c))/config.env
```

The provider template lives at `tools/claude-provider-router/config.env.example`.

Run Claude Code through the short router command:

```bash
c 0
c team 2 0
```

Check plan burn:

```bash
burnkit status --refresh
```

Uninstall everything:

```bash
burnkit uninstall all
```

## Let Your Agent Install It

Copy this into your AI coding agent and let it handle the install:

```text
Install BurnKit for me in this repo.

Rules:
- First run `scripts/e2e-install-verify.sh --dry-run`.
- Run `bin/burnkit doctor`.
- Dry-run each BurnKit install target before any real install:
  - `bin/burnkit install router --dry-run`
  - `bin/burnkit install tabs --dry-run --skip-python-check`
  - `bin/burnkit install burn --dry-run`
- Do not overwrite `tools/claude-provider-router/config.env`. If it already exists, preserve it byte-for-byte.
- Before real install, tell me exactly what files and system state will be changed, then wait for my explicit confirmation.
- After I confirm, run the real install and verify it with `scripts/e2e-install-verify.sh --real`.
```

The important part: `config.env` contains provider tokens. A correct agent must preserve it if it already exists.

## The Loop It Creates

```text
1. Start more AI sessions.
2. Watch idle tabs turn green, yellow, and red.
3. Use Burn AI to see whether your 5h / 7d plan windows are being wasted.
4. Hit the human scheduling limit.
5. Start designing a real agent harness.
```

This is the point. BurnKit is not a forever-babysitting tool. It is a pressure rig. It squeezes your idle time, your context switching, and your confidence that one human can keep ten agent sessions moving by hand.

At the limit, the useful questions stop being motivational and start being architectural:

```text
Why does it keep asking me?
Why can't it decide the next step?
Why am I still the human event loop?
Why can't these sessions queue, split work, verify, and ship?
```

Exactly. You start harnessing.

## Command Map

| Command | Purpose |
|---------|---------|
| `burnkit doctor` | Check local prerequisites and tool readiness |
| `burnkit install router` | Run the router internal installer; create `config.env` from the template only when missing, and install the `c` shim |
| `burnkit install tabs` | Install iTerm2 Tab Color through the unified BurnKit entrypoint |
| `burnkit install burn` | Install/build Burn AI, then run `burn-ai install` |
| `burnkit install all` | Install CLI shim, router, tab color, and Burn AI in order |
| `burnkit uninstall all` | Uninstall tab color, Burn AI, and CLI shim |
| `c 0` | Start Claude Code through provider config `0` |
| `c team 2 0` | Start Agent Team routing: leader on `2`, teammate on `0` |
| `burnkit router 0` | Long-form compatibility wrapper for `c 0` |
| `burnkit burn doctor` | Forward to Burn AI CLI |
| `burnkit status --refresh` | Refresh and print plan usage state |

Each tool still owns its own runtime files, safety checks, and uninstall path.

## Tab Pressure

| Color | Meaning | Operator Signal |
|-------|---------|-----------------|
| Green | AI just finished and is waiting | Collect the result now |
| Yellow | It has waited for a while | Your parallelism is leaking |
| Red | It has waited too long | The machine is ready; the human is late |
| White | Active tab, processing, or clean state | No attention needed here |

Only inactive tabs get colored. The tab you are looking at stays white because notifications should point at what you are missing.

## Repository Layout

```text
.
├── bin/
│   └── burnkit
├── tools/
│   ├── claude-provider-router/
│   ├── iterm2-tab-color/
│   └── burn-ai/
├── assets/
├── package.json
├── AGENTS.md
├── CLAUDE.md
├── README.md
└── README.zh-CN.md
```

## Tool Docs

- [Claude Provider Router](tools/claude-provider-router/README.md)
- [iTerm2 Tab Color](tools/iterm2-tab-color/README.md)
- [iTerm2 Tab Color Chinese README](tools/iterm2-tab-color/README.zh-CN.md)
- [Burn AI](tools/burn-ai/README.md)

## Safety Notes

- `tools/claude-provider-router/config.env` contains tokens and must not be committed.
- Burn AI does not manage login state, credentials, or private usage APIs. It reads local usage data already produced by Claude Code and Codex.
- Burn AI does not overwrite existing Claude Code status line scripts. If one exists, it asks before installing a wrapper; skipping prints manual integration steps and explains which Claude features stay unavailable.
- Tab color behavior, state cleanup, process detection, hook events, and daemon scheduling are behavior changes. Do not bundle them with docs or release polish.

## Development Checks

For the release entry point:

```bash
bash -n bin/burnkit
bin/burnkit --help
bin/burnkit doctor
scripts/e2e-install-verify.sh --dry-run
```

For real install verification on a local machine:

```bash
scripts/e2e-install-verify.sh --real
```

The e2e verifier checks both router install paths: missing `config.env` is created from the template with mode `600`, and an existing `tools/claude-provider-router/config.env` is preserved byte-for-byte with its original permissions.

For iTerm2 Tab Color changes:

```bash
bash -n tools/iterm2-tab-color/install-core.sh tools/iterm2-tab-color/uninstall-core.sh tools/iterm2-tab-color/install.sh tools/iterm2-tab-color/uninstall.sh tools/iterm2-tab-color/tab_color_hook.sh
python3 -m py_compile tools/iterm2-tab-color/tab_color_daemon.py tools/iterm2-tab-color/reset_tab.py tools/iterm2-tab-color/test_daemon.py
python3 -m unittest tools/iterm2-tab-color/test_daemon.py
```

For Burn AI changes:

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
git diff --check
```

When installing Burn AI from this repository, prefer `bin/burnkit install burn` or `bin/burnkit install all`; those paths rebuild `tools/burn-ai` before copying the runtime into `~/.burn-ai/app`.

## Contributors

<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->
<!-- prettier-ignore-start -->
<!-- markdownlint-disable -->
<table>
  <tbody>
    <tr>
      <td align="center" valign="top" width="14.28%">
        <a href="https://github.com/huajuan404">
          <img src="https://avatars.githubusercontent.com/u/142046284?v=4&s=100" width="100px;" alt="huajuan404"/>
        </a>
        <br />
        <a href="https://github.com/huajuan404">huajuan404</a>
        <br />
        <a href="#" title="Code">💻</a>
        <a href="#" title="Documentation">📖</a>
        <a href="#" title="Design">🎨</a>
      </td>
    </tr>
  </tbody>
</table>
<!-- markdownlint-restore -->
<!-- prettier-ignore-end -->
<!-- ALL-CONTRIBUTORS-LIST:END -->

## License

[MIT](LICENSE)
