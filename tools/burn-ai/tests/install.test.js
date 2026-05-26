import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import assert from "node:assert/strict";
import { buildPaths } from "../dist/paths.js";
import { installClaudeStatusLine, uninstall } from "../dist/install.js";
import { writeJsonAtomic } from "../dist/fs-util.js";

function createCustomStatusLine(home) {
  const paths = buildPaths(home);
  const script = path.join(home, "custom-statusline.sh");
  fs.mkdirSync(path.dirname(paths.claudeSettingsFile), { recursive: true });
  fs.writeFileSync(script, "#!/usr/bin/env bash\nprintf \"custom\"\n", { mode: 0o755 });
  writeJsonAtomic(paths.claudeSettingsFile, {
    statusLine: {
      type: "command",
      command: script,
    },
  });
  return { paths, script };
}

test("installClaudeStatusLine wraps custom status line after confirmation", () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "burn-ai-install-"));
  const { paths, script } = createCustomStatusLine(home);

  const messages = installClaudeStatusLine(paths, {
    dryRun: false,
    confirmStatusLineUpdate: () => true,
  });

  const settings = JSON.parse(fs.readFileSync(paths.claudeSettingsFile, "utf8"));
  const wrapper = fs.readFileSync(paths.claudeStatusLineScript, "utf8");
  assert.equal(settings.statusLine.command, paths.claudeStatusLineScript);
  assert.equal(fs.readFileSync(script, "utf8"), "#!/usr/bin/env bash\nprintf \"custom\"\n");
  assert.match(wrapper, /ingest claude-statusline/);
  assert.match(wrapper, /ORIGINAL_COMMAND=/);
  assert.match(wrapper, /\/bin\/sh -c "\$ORIGINAL_COMMAND"/);
  assert.ok(messages.some((message) => message.includes("Updated Claude status line script")));
});

test("installClaudeStatusLine quotes generated ingest command in wrapper", () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "burn ai install-"));
  const { paths } = createCustomStatusLine(home);

  installClaudeStatusLine(paths, {
    dryRun: false,
    confirmStatusLineUpdate: () => true,
  });

  const wrapper = fs.readFileSync(paths.claudeStatusLineScript, "utf8");
  assert.match(wrapper, new RegExp(`\\| '${process.execPath.replaceAll("'", "'\\\\''")}' '`));
  assert.match(wrapper, /\/\.burn-ai\/app\/dist\/cli\.js'/);
});

test("installClaudeStatusLine detects integrated scripts invoked through an interpreter", () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "burn-ai-install-"));
  const paths = buildPaths(home);
  const script = path.join(home, "custom-statusline.sh");
  fs.mkdirSync(path.dirname(paths.claudeSettingsFile), { recursive: true });
  fs.writeFileSync(script, "#!/usr/bin/env bash\nburn-ai ingest claude-statusline\n", { mode: 0o755 });
  writeJsonAtomic(paths.claudeSettingsFile, {
    statusLine: {
      type: "command",
      command: `bash ${script}`,
    },
  });

  const messages = installClaudeStatusLine(paths, {
    dryRun: false,
    confirmStatusLineUpdate: () => {
      throw new Error("should not prompt when script already contains ingest");
    },
  });

  assert.deepEqual(messages, ["Claude status line already includes burn-ai ingest."]);
  assert.equal(fs.existsSync(paths.claudeStatusLineScript), false);
});

test("installClaudeStatusLine skips custom script when confirmation is rejected", () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "burn-ai-install-"));
  const { paths, script } = createCustomStatusLine(home);

  const messages = installClaudeStatusLine(paths, {
    dryRun: false,
    confirmStatusLineUpdate: () => false,
  });

  assert.equal(fs.readFileSync(script, "utf8"), "#!/usr/bin/env bash\nprintf \"custom\"\n");
  assert.equal(fs.existsSync(`${script}.burn-ai.bak`), false);
  assert.ok(messages.some((message) => message.includes("Skipped Claude status line update.")));
  assert.ok(messages.some((message) => message.includes("Claude usage will stay unavailable")));
});

test("uninstall restores wrapped custom status line command", () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "burn-ai-install-"));
  const { paths, script } = createCustomStatusLine(home);
  fs.mkdirSync(path.dirname(paths.launchAgentFile), { recursive: true });
  fs.writeFileSync(paths.launchAgentFile, "plist", "utf8");

  installClaudeStatusLine(paths, {
    dryRun: false,
    confirmStatusLineUpdate: () => true,
  });

  const originalHome = process.env.HOME;
  const originalPluginDir = process.env.BURN_AI_PLUGIN_DIR;
  process.env.HOME = home;
  // Sandbox the SwiftBar plugin dir so uninstall can't touch the real menubar plugin.
  process.env.BURN_AI_PLUGIN_DIR = path.join(home, "swiftbar");
  try {
    const messages = uninstall({ dryRun: false });
    const settings = JSON.parse(fs.readFileSync(paths.claudeSettingsFile, "utf8"));
    assert.equal(settings.statusLine.command, script);
    assert.ok(messages.some((message) => message.includes("Restored user-managed Claude status line.")));
  } finally {
    if (originalHome === undefined) {
      delete process.env.HOME;
    } else {
      process.env.HOME = originalHome;
    }
    if (originalPluginDir === undefined) {
      delete process.env.BURN_AI_PLUGIN_DIR;
    } else {
      process.env.BURN_AI_PLUGIN_DIR = originalPluginDir;
    }
  }
});
