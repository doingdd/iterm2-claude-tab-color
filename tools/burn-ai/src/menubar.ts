import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { ensureDir, isFile } from "./fs-util.js";
import { formatDurationUntil, formatProviderLabel } from "./format.js";
import { buildPaths } from "./paths.js";
import { loadDisplayStatusSnapshot } from "./runtime.js";
import { BurnState, RuntimePaths, StatusSnapshot } from "./types.js";

const MARKER = "burn-ai managed SwiftBar plugin";
const SWIFTBAR_APP_PATHS = ["/Applications/SwiftBar.app", path.join(process.env.HOME ?? "", "Applications", "SwiftBar.app")];

const STATE_LABEL: Record<BurnState, string> = {
  RAW: "Learning",
  UNDER_BURN: "Low",
  ON_TRACK: "On Track",
  OVER_BURN: "Fast",
  LIMIT_RISK: "Limit",
};

// Local copies of official site icons keep SwiftBar rendering offline and stable.
const PROVIDER_ICON_ASSET: Record<string, string> = {
  claude: "claude-code-official.png",
  codex: "codex-openai-official.png",
  glm: "glm-zhipu-official.png",
  deepseek: "deepseek-official.png",
  minimax: "minimax-official.png",
};

const PROVIDER_ICON_FALLBACK: Record<string, string> = {
  claude: "sparkles",
  codex: "curlybraces.square.fill",
  glm: "cpu",
  deepseek: "bubble.left.and.bubble.right",
  minimax: "waveform.path.ecg",
};

const TITLE_ICON_ASSET = "provider-icons-official.png";
const ALERT_COLOR = "#FF453A,#FF6961";
const WARNING_COLOR = "#FF9F0A,#FFD60A";
const OK_COLOR = "#248A3D,#30D158";
const RAW_COLOR = "#6B7280,#8E8E93";

const STATE_COLOR: Record<BurnState, string> = {
  RAW: RAW_COLOR,
  UNDER_BURN: WARNING_COLOR,
  ON_TRACK: OK_COLOR,
  OVER_BURN: ALERT_COLOR,
  LIMIT_RISK: ALERT_COLOR,
};

const STATE_PRIORITY: Record<BurnState, number> = {
  LIMIT_RISK: 0,
  OVER_BURN: 1,
  UNDER_BURN: 2,
  RAW: 3,
  ON_TRACK: 4,
};

const TEXT_COLOR = "#111827,#F9FAFB";
const MUTED_COLOR = "#6B7280,#A1A1AA";
const ROW_FONT = "Menlo";
const TITLE_PROVIDER_ORDER = ["codex", "claude", "glm", "deepseek", "minimax"];
const TITLE_SEPARATOR = "│";
const METER_WIDTH = 12;
const ASSET_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "assets");
const imageCache = new Map<string, string | null>();
const TITLE_IMAGE_SCALE = 2;
const titleImageCache = new Map<string, { image: string; width: number; height: number } | null>();

const TITLE_IMAGE_SCRIPT = `
ObjC.import('AppKit');
ObjC.import('Foundation');

function hexColor(hex) {
  const value = hex.replace('#', '');
  const r = parseInt(value.slice(0, 2), 16) / 255;
  const g = parseInt(value.slice(2, 4), 16) / 255;
  const b = parseInt(value.slice(4, 6), 16) / 255;
  return $.NSColor.colorWithSRGBRedGreenBlueAlpha(r, g, b, 1);
}

function drawImage(payload, variant) {
  const scale = payload.scale || 1;
  const height = payload.height * scale;
  const fontSize = payload.fontSize * scale;
  const iconSize = payload.iconSize * scale;
  const paddingX = payload.paddingX * scale;
  const iconTextGap = payload.iconTextGap * scale;
  const segmentGap = payload.segmentGap * scale;
  const attrs = $.NSMutableDictionary.alloc.init;
  attrs.setObjectForKey($.NSFont.menuBarFontOfSize(fontSize), $.NSFontAttributeName);
  attrs.setObjectForKey(hexColor(variant.textColor), $.NSForegroundColorAttributeName);

  const dividerAttrs = $.NSMutableDictionary.alloc.init;
  dividerAttrs.setObjectForKey($.NSFont.menuBarFontOfSize(fontSize), $.NSFontAttributeName);
  dividerAttrs.setObjectForKey(hexColor(variant.dividerColor), $.NSForegroundColorAttributeName);

  const dividerWidth = Math.ceil($(payload.divider).sizeWithAttributes(dividerAttrs).width);
  let width = paddingX * 2;
  payload.segments.forEach((segment, index) => {
    if (index > 0) {
      width += segmentGap + dividerWidth + segmentGap;
    }
    const hasIcon = Boolean(segment.iconPath);
    width += (hasIcon ? iconSize + iconTextGap : 0) + Math.ceil($(segment.text).sizeWithAttributes(attrs).width);
  });
  width = Math.max(payload.minWidth * scale, width);

  const rep = $.NSBitmapImageRep.alloc.initWithBitmapDataPlanesPixelsWidePixelsHighBitsPerSampleSamplesPerPixelHasAlphaIsPlanarColorSpaceNameBitmapFormatBytesPerRowBitsPerPixel(
    null, width, height, 8, 4, true, false, $.NSDeviceRGBColorSpace, $.NSBitmapFormatAlphaPremultipliedLast, 0, 0
  );
  const context = $.NSGraphicsContext.graphicsContextWithBitmapImageRep(rep);
  $.NSGraphicsContext.setCurrentContext(context);
  context.setShouldAntialias(true);
  context.setImageInterpolation($.NSImageInterpolationHigh);

  let x = paddingX;
  const iconY = Math.floor((height - iconSize) / 2);
  const textY = Math.floor((height - fontSize - (2 * scale)) / 2);
  payload.segments.forEach((segment, index) => {
    if (index > 0) {
      x += segmentGap;
      $(payload.divider).drawAtPointWithAttributes($.NSMakePoint(x, textY), dividerAttrs);
      x += dividerWidth + segmentGap;
    }
    if (segment.iconPath) {
      const icon = $.NSImage.alloc.initWithContentsOfFile($(segment.iconPath));
      if (icon) {
        icon.drawInRectFromRectOperationFraction($.NSMakeRect(x, iconY, iconSize, iconSize), $.NSZeroRect, $.NSCompositingOperationSourceOver, 1);
      }
      x += iconSize + iconTextGap;
    }
    $(segment.text).drawAtPointWithAttributes($.NSMakePoint(x, textY), attrs);
    x += Math.ceil($(segment.text).sizeWithAttributes(attrs).width);
  });

  const png = rep.representationUsingTypeProperties($.NSBitmapImageFileTypePNG, $.NSDictionary.alloc.init);
  return {
    image: ObjC.unwrap(png.base64EncodedStringWithOptions(0)),
    width: Math.ceil(width / scale),
    height: payload.height,
  };
}

function run(argv) {
  const payload = JSON.parse(argv[0]);
  return JSON.stringify({
    light: drawImage(payload, payload.light),
    dark: drawImage(payload, payload.dark),
  });
}
`;

function appCliPath(paths: RuntimePaths) {
  return path.join(paths.stateDir, "app", "dist", "cli.js");
}

const COMPACT_MODE_FILE = "compact-mode";

function compactModeFilePath() {
  return path.join(buildPaths().stateDir, COMPACT_MODE_FILE);
}

export function readCompactMode(): boolean {
  try {
    return fs.existsSync(compactModeFilePath());
  } catch {
    return false;
  }
}

export function toggleCompactMode(): boolean {
  const filePath = compactModeFilePath();
  if (fs.existsSync(filePath)) {
    fs.rmSync(filePath, { force: true });
    return false;
  }
  fs.writeFileSync(filePath, "");
  return true;
}

function configuredSwiftBarPluginDir(paths: RuntimePaths) {
  try {
    const configured = execFileSync("defaults", ["read", "com.ameba.SwiftBar", "PluginDirectory"], {
      encoding: "utf8",
    }).trim();
    return configured || paths.swiftBarPluginDir;
  } catch {
    return paths.swiftBarPluginDir;
  }
}

export function swiftBarPluginPath(paths: RuntimePaths = buildPaths()) {
  const pluginDir = configuredSwiftBarPluginDir(paths);
  return {
    dir: pluginDir,
    file: path.join(pluginDir, "burn-ai.1m.js"),
  };
}

export function isSwiftBarInstalled() {
  return SWIFTBAR_APP_PATHS.some((appPath) => appPath && fs.existsSync(appPath));
}

function swiftBarAppPath() {
  return SWIFTBAR_APP_PATHS.find((appPath) => appPath && fs.existsSync(appPath)) ?? null;
}

function brewPath() {
  const candidates = ["/opt/homebrew/bin/brew", "/usr/local/bin/brew"];
  const found = candidates.find((candidate) => fs.existsSync(candidate));
  if (found) {
    return found;
  }
  try {
    return execFileSync("which", ["brew"], { encoding: "utf8" }).trim();
  } catch {
    return null;
  }
}

export function ensureSwiftBarInstalled(options: { dryRun?: boolean } = {}) {
  const dryRun = options.dryRun ?? false;
  if (isSwiftBarInstalled()) {
    return ["SwiftBar already installed."];
  }

  const brew = brewPath();
  if (!brew) {
    return ["SwiftBar is missing and Homebrew is unavailable; install SwiftBar manually, then run burn-ai menubar install."];
  }

  if (dryRun) {
    return [`[dry-run] would install SwiftBar with ${brew} install --cask swiftbar`];
  }

  execFileSync(brew, ["install", "--cask", "swiftbar"], {
    env: { ...process.env, HOMEBREW_NO_AUTO_UPDATE: "1" },
    stdio: "inherit",
  });
  return ["Installed SwiftBar with Homebrew cask."];
}

export function openSwiftBar() {
  const appPath = swiftBarAppPath();
  if (!appPath) {
    return ["SwiftBar is not installed; skipping launch."];
  }
  try {
    execFileSync("osascript", ["-e", 'quit app "SwiftBar"'], { stdio: "ignore" });
  } catch {
    // SwiftBar may not be running yet.
  }
  try {
    execFileSync("open", [appPath], { stdio: "ignore" });
    execFileSync("sleep", ["1"], { stdio: "ignore" });
    return ["Opened SwiftBar.", ...clearSwiftBarStatusItemVisibility()];
  } catch (error) {
    return [`SwiftBar installed, but launch failed: ${error instanceof Error ? error.message : String(error)}`];
  }
}

export function addSwiftBarToLoginItems(options: { dryRun?: boolean } = {}) {
  const dryRun = options.dryRun ?? false;
  const appPath = swiftBarAppPath();
  if (!appPath) {
    return ["SwiftBar is not installed; skipping login item."];
  }

  try {
    const result = execFileSync("osascript", [
      "-e", 'tell application "System Events" to get the name of every login item',
    ], { encoding: "utf8" });
    if (result.includes("SwiftBar")) {
      return ["SwiftBar already in login items."];
    }
  } catch {
    // Permission or access issue; try to add anyway.
  }

  if (dryRun) {
    return [`[dry-run] would add SwiftBar to login items: ${appPath}`];
  }

  try {
    execFileSync("osascript", [
      "-e", `tell application "System Events" to make login item at end with properties {name:"SwiftBar", path:${JSON.stringify(appPath)}, hidden:false}`,
    ]);
    return ["Added SwiftBar to login items (auto-starts on login)."];
  } catch (error) {
    return [`Failed to add SwiftBar to login items: ${error instanceof Error ? error.message : String(error)}. Add manually in System Settings → General → Login Items.`];
  }
}

function swiftBarEscape(value: string) {
  return value.replaceAll("|", "\\|");
}

function swiftBarParamValue(value: string | number | boolean) {
  return String(value).replaceAll(" ", "\\ ");
}

function line(title: string, params: Record<string, string | number | boolean | undefined> = {}) {
  const renderedParams = Object.entries(params)
    .filter((entry): entry is [string, string | number | boolean] => entry[1] !== undefined)
    .map(([key, value]) => `${key}=${swiftBarParamValue(value)}`);
  if (renderedParams.length === 0) {
    return swiftBarEscape(title);
  }
  return `${swiftBarEscape(title)} | ${renderedParams.join(" ")}`;
}

function imageAssetBase64(name: string) {
  if (imageCache.has(name)) {
    return imageCache.get(name);
  }
  try {
    const encoded = fs.readFileSync(path.join(ASSET_DIR, name)).toString("base64");
    imageCache.set(name, encoded);
    return encoded;
  } catch {
    imageCache.set(name, null);
    return null;
  }
}

function titleProviders(snapshot: StatusSnapshot) {
  return [...snapshot.providers].sort((left, right) => {
    const leftIndex = TITLE_PROVIDER_ORDER.indexOf(left.usage.provider);
    const rightIndex = TITLE_PROVIDER_ORDER.indexOf(right.usage.provider);
    const leftRank = leftIndex === -1 ? TITLE_PROVIDER_ORDER.length : leftIndex;
    const rightRank = rightIndex === -1 ? TITLE_PROVIDER_ORDER.length : rightIndex;
    return leftRank - rightRank;
  });
}

function titleSegment(provider: StatusSnapshot["providers"][number]) {
  const usage = provider.usage;
  if (usage.balance && usage.windows.length === 0) {
    const currency = usage.balance.currency === "CNY" ? "¥" : "$";
    return `${currency}${usage.balance.total}`;
  }
  const fiveHour = provider.analysis.fiveHour;
  const sevenDay = provider.analysis.sevenDay;
  const titleFive = fiveHour ? `${Math.round(fiveHour.usedPercent)}%` : "--";
  const titleSeven = sevenDay ? `${Math.round(sevenDay.usedPercent)}%` : "--";
  return `5H:${titleFive},7D:${titleSeven}`;
}

function providerIconPath(provider: string) {
  const asset = PROVIDER_ICON_ASSET[provider];
  if (!asset) {
    return null;
  }
  const iconPath = path.join(ASSET_DIR, asset);
  return fs.existsSync(iconPath) ? iconPath : null;
}

function titleImageValue(providers: StatusSnapshot["providers"]) {
  const segments = providers.map((provider) => ({
    provider: provider.usage.provider,
    text: titleSegment(provider),
    iconPath: providerIconPath(provider.usage.provider),
  }));
  if (segments.length === 0) {
    return null;
  }

  const payload = {
    segments,
    divider: TITLE_SEPARATOR,
    scale: TITLE_IMAGE_SCALE,
    height: 22,
    minWidth: 1,
    paddingX: 0,
    iconSize: 16,
    iconTextGap: 4,
    segmentGap: 9,
    fontSize: 13,
    light: {
      textColor: "FFFFFF",
      dividerColor: "E5E7EB",
    },
    dark: {
      textColor: "FFFFFF",
      dividerColor: "E5E7EB",
    },
  };
  const cacheKey = JSON.stringify(payload);
  if (titleImageCache.has(cacheKey)) {
    return titleImageCache.get(cacheKey);
  }

  try {
    const output = execFileSync("/usr/bin/osascript", ["-l", "JavaScript", "-e", TITLE_IMAGE_SCRIPT, cacheKey], {
      encoding: "utf8",
      timeout: 2500,
    }).trim();
    const images = JSON.parse(output) as {
      light?: { image?: string; width?: number; height?: number };
      dark?: { image?: string; width?: number; height?: number };
    };
    const value = images.light?.image && images.dark?.image && images.light.width && images.light.height
      ? {
        image: `${images.light.image},${images.dark.image}`,
        width: images.light.width,
        height: images.light.height,
      }
      : null;
    titleImageCache.set(cacheKey, value);
    return value;
  } catch {
    titleImageCache.set(cacheKey, null);
    return null;
  }
}

function targetLabel(provider: StatusSnapshot["providers"][number]) {
  const target = provider.analysis.target;
  if (!target) {
    return "Target learning baseline";
  }
  return `Target ${target.minPercent.toFixed(1)}-${target.maxPercent.toFixed(1)}%`;
}

function windowByName(provider: StatusSnapshot["providers"][number], name: "five_hour" | "seven_day") {
  return provider.usage.windows.find((window) => window.name === name);
}

function muted(text: string) {
  return line(text, { color: MUTED_COLOR, size: 12 });
}

function meter(usedPercent: number, width = METER_WIDTH) {
  const usedCells = Math.max(0, Math.min(width, Math.round((usedPercent / 100) * width)));
  return `${"#".repeat(usedCells)}${"-".repeat(width - usedCells)}`;
}

function usageLine(label: "5h" | "7d", usedPercent: number, resetsAt: string, color: string) {
  const percent = `${Math.round(usedPercent).toString().padStart(3)}%`;
  return line(`${label}  ${meter(usedPercent)}  ${percent}  reset ${formatDurationUntil(resetsAt)}`, {
    color,
    font: ROW_FONT,
    size: 12,
  });
}

function providerBadge(provider: StatusSnapshot["providers"][number]) {
  const fiveHour = provider.analysis.fiveHour;
  if (!fiveHour) {
    return STATE_LABEL[provider.analysis.state];
  }
  return `${Math.round(fiveHour.usedPercent)}%`;
}

function providerIconParams(provider: string) {
  const image = imageAssetBase64(PROVIDER_ICON_ASSET[provider] ?? "");
  if (image) {
    return { image };
  }
  return { sfimage: PROVIDER_ICON_FALLBACK[provider] ?? "terminal.fill" };
}

function titleIconParams() {
  const image = imageAssetBase64(TITLE_ICON_ASSET);
  if (image) {
    return { image };
  }
  return { sfimage: "flame.fill", sfcolor: RAW_COLOR };
}

function maxProviderAge(snapshot: StatusSnapshot) {
  return Math.max(0, ...snapshot.providers.map((item) => item.meta.ageSeconds));
}

function issueLabel(code: string) {
  if (code === "CLAUDE_INGEST_MISSING") {
    return "Claude not connected";
  }
  if (code === "GLM_API_KEY_MISSING") {
    return "GLM API key not set";
  }
  if (code === "DEEPSEEK_API_KEY_MISSING") {
    return "DeepSeek API key not set";
  }
  if (code === "MINIMAX_API_KEY_MISSING") {
    return "MiniMax API key not set";
  }
  if (code === "USAGE_STALE") {
    return "Usage data is stale";
  }
  if (code === "STATUS_MISSING") {
    return "Status not ready";
  }
  return code.replaceAll("_", " ").toLowerCase();
}

export function renderMenuBar(snapshot: StatusSnapshot = loadDisplayStatusSnapshot()) {
  const compact = readCompactMode();
  const providers = titleProviders(snapshot);
  const title = providers.map(titleSegment).join(` ${TITLE_SEPARATOR} `);
  const toggleParams = {
    bash: process.execPath,
    param1: appCliPath(buildPaths()),
    param2: "menubar",
    param3: "toggle-compact",
    terminal: false,
    refresh: true,
    color: TEXT_COLOR,
    sfimage: compact ? "rectangle.expand.vertical" : "rectangle.compress.vertical",
  };
  if (!title) {
    return [
      line("Burn AI  No Usage", { sfimage: "flame.fill", sfcolor: RAW_COLOR }),
      "---",
      line("No provider usage available", { color: MUTED_COLOR }),
      line(compact ? "Expand" : "Collapse", toggleParams),
      line("Refresh now", { refresh: true, color: TEXT_COLOR, sfimage: "arrow.clockwise" }),
    ].join("\n");
  }

  const topState = [...providers].sort((left, right) => (
    STATE_PRIORITY[left.analysis.state] - STATE_PRIORITY[right.analysis.state]
  ))[0]?.analysis.state
    ?? "RAW";
  const titleImage = titleImageValue(providers);
  const lines = [
    compact
      ? line("", {
        sfimage: "flame.fill",
        sfcolor: STATE_COLOR[topState],
        dropdown: false,
        tooltip: title,
      })
      : titleImage
      ? line("", {
        image: titleImage.image,
        width: titleImage.width,
        height: titleImage.height,
        dropdown: false,
        tooltip: title,
      })
      : line(title, {
        ...titleIconParams(),
        dropdown: false,
      }),
    "---",
    line("Burn AI", {
      color: TEXT_COLOR,
      size: 15,
      sfimage: "flame.fill",
      sfcolor: STATE_COLOR[topState],
      badge: snapshot.profile.toUpperCase(),
    }),
    muted(`Data age ${maxProviderAge(snapshot)}s`),
    "---",
  ];

  for (const item of providers) {
    const color = STATE_COLOR[item.analysis.state];
    const five = windowByName(item, "five_hour");
    const seven = windowByName(item, "seven_day");

    if (item.usage.balance && item.usage.windows.length === 0) {
      const currency = item.usage.balance.currency === "CNY" ? "¥" : "$";
      const balanceText = `${currency}${item.usage.balance.total}`;
      const availableLabel = item.usage.balance.isAvailable ? "Available" : "Depleted";
      lines.push(line(`${formatProviderLabel(item.usage.provider)}  ${availableLabel}`, {
        ...providerIconParams(item.usage.provider),
        color: item.usage.balance.isAvailable ? OK_COLOR : ALERT_COLOR,
        size: 14,
        badge: balanceText,
      }));
      lines.push(line(`Balance  ${balanceText}`, {
        color: TEXT_COLOR,
        font: ROW_FONT,
        size: 12,
      }));
      lines.push(line(item.analysis.message, { color: MUTED_COLOR, size: 12, length: 84 }));
      lines.push("---");
      continue;
    }

    lines.push(line(`${formatProviderLabel(item.usage.provider)}  ${STATE_LABEL[item.analysis.state]}`, {
      ...providerIconParams(item.usage.provider),
      color,
      size: 14,
      badge: providerBadge(item),
    }));
    if (five) {
      lines.push(usageLine("5h", five.usedPercent, five.resetsAt, color));
    }
    if (seven) {
      lines.push(usageLine("7d", seven.usedPercent, seven.resetsAt, TEXT_COLOR));
    }
    lines.push(muted(targetLabel(item)));
    lines.push(line(item.analysis.message, { color: MUTED_COLOR, size: 12, length: 84 }));
    lines.push("---");
  }

  for (const issue of snapshot.issues) {
    const color = issue.severity === "error" ? ALERT_COLOR : WARNING_COLOR;
    lines.push(line(`${issue.severity.toUpperCase()}  ${issueLabel(issue.code)}`, {
      color,
      size: 13,
      sfimage: "exclamationmark.triangle.fill",
      sfcolor: color,
    }));
    lines.push(muted(issue.message));
  }

  if (snapshot.issues.length > 0) {
    lines.push("---");
  }
  lines.push(line(compact ? "Expand" : "Collapse", toggleParams));
  lines.push(line("Refresh now", {
    refresh: true,
    color: TEXT_COLOR,
    sfimage: "arrow.clockwise",
  }));
  return lines.join("\n");
}

function pluginScript(paths: RuntimePaths) {
  return `#!${process.execPath}
// ${MARKER}
// <swiftbar.title>Burn AI</swiftbar.title>
// <swiftbar.version>v0.1.0</swiftbar.version>
// <swiftbar.author>Burn AI</swiftbar.author>
// <swiftbar.desc>Local Claude Code and Codex burn-rate monitor.</swiftbar.desc>
// <swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>
// <swiftbar.hideAbout>true</swiftbar.hideAbout>
// <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
// <swiftbar.hideLastUpdated>true</swiftbar.hideLastUpdated>
// <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
// <swiftbar.refreshOnOpen>true</swiftbar.refreshOnOpen>
const { spawnSync } = require("node:child_process");
const result = spawnSync(${JSON.stringify(process.execPath)}, [${JSON.stringify(appCliPath(paths))}, "menubar", "render"], {
  encoding: "utf8",
});
if (result.error) {
  console.log("Burn ERR");
  console.log("---");
  console.log(result.error.message);
  process.exit(0);
}
if (result.status !== 0) {
  console.log("Burn ERR");
  console.log("---");
  console.log(result.stderr || result.stdout || "burn-ai menubar render failed");
  process.exit(0);
}
process.stdout.write(result.stdout);
`;
}

function isManagedPlugin(paths: RuntimePaths) {
  const plugin = swiftBarPluginPath(paths);
  try {
    return fs.readFileSync(plugin.file, "utf8").includes(MARKER);
  } catch {
    return false;
  }
}

export function swiftBarStatusItemVisibilityKeys(defaultsOutput: string) {
  return defaultsOutput
    .split("\n")
    .map((line) => line.match(/^\s*"?((?:NSStatusItem Visible)[^"=]*)"?\s*=/)?.[1]?.trim())
    .filter((key): key is string => Boolean(key));
}

function clearSwiftBarStatusItemVisibility(options: { dryRun?: boolean } = {}) {
  let keys: string[];
  try {
    keys = swiftBarStatusItemVisibilityKeys(execFileSync("defaults", ["read", "com.ameba.SwiftBar"], {
      encoding: "utf8",
    }));
  } catch {
    return [];
  }

  if (keys.length === 0) {
    return [];
  }

  if (options.dryRun) {
    return [`[dry-run] would clear SwiftBar hidden status item cache: ${keys.join(", ")}`];
  }

  for (const key of keys) {
    try {
      execFileSync("defaults", ["delete", "com.ameba.SwiftBar", key], { stdio: "ignore" });
    } catch {
      // Ignore races with SwiftBar rewriting or deleting the same key.
    }
  }
  return [`Cleared SwiftBar hidden status item cache: ${keys.join(", ")}`];
}

export function installMenuBar(options: { dryRun?: boolean } = {}) {
  const dryRun = options.dryRun ?? false;
  const paths = buildPaths();
  const plugin = swiftBarPluginPath(paths);
  if (dryRun) {
    return [
      `[dry-run] would write SwiftBar plugin: ${plugin.file}`,
      ...clearSwiftBarStatusItemVisibility({ dryRun }),
    ];
  }
  if (isFile(plugin.file) && !isManagedPlugin(paths)) {
    return [`SwiftBar plugin already exists and is not managed by burn-ai: ${plugin.file}`];
  }
  ensureDir(plugin.dir);
  fs.writeFileSync(plugin.file, pluginScript(paths), { mode: 0o755 });
  return [
    `Installed SwiftBar plugin: ${plugin.file}`,
    ...clearSwiftBarStatusItemVisibility(),
    "Open SwiftBar and set its plugin folder to the Burn AI plugin directory if it is not already configured.",
    `Plugin directory: ${plugin.dir}`,
  ];
}

export function uninstallMenuBar(options: { dryRun?: boolean } = {}) {
  const dryRun = options.dryRun ?? false;
  const paths = buildPaths();
  const plugin = swiftBarPluginPath(paths);
  if (!isFile(plugin.file)) {
    return ["No Burn AI SwiftBar plugin installed."];
  }
  if (!isManagedPlugin(paths)) {
    return [`SwiftBar plugin is user-managed; leaving it unchanged: ${plugin.file}`];
  }
  if (dryRun) {
    return [`[dry-run] would remove SwiftBar plugin: ${plugin.file}`];
  }
  fs.rmSync(plugin.file, { force: true });
  return [`Removed SwiftBar plugin: ${plugin.file}`];
}
