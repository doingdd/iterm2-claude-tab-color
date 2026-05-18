import fs from "node:fs";
import path from "node:path";
import { readConfig } from "./config.js";
import { collectCodexUsage } from "./codex.js";
import { readJsonFile } from "./fs-util.js";
import { collectGlmUsage } from "./glm.js";
import { buildPaths } from "./paths.js";
import { loadLatestUsage, loadSamples, saveUsage } from "./store.js";
import { BurnAnalysis, BurnProfile, ProviderUsage, RuntimePaths, StatusIssue, StatusSnapshot } from "./types.js";
import {
  createStatusSnapshot,
  loadStatusSnapshot,
  refreshStatusSnapshotFreshness,
  saveStatusSnapshot,
} from "./status.js";

export function loadFixtureUsages(fixturesDir: string): ProviderUsage[] {
  const providers = ["claude", "codex", "glm"] as const;
  return providers
    .map((provider) => readJsonFile<ProviderUsage>(path.join(fixturesDir, provider, "latest.json")))
    .filter((item): item is ProviderUsage => item !== null);
}

export async function collectLocalState(options: { fixtures?: boolean } = {}): Promise<{
  usages: ProviderUsage[];
  issues: StatusIssue[];
}> {
  if (options.fixtures) {
    return { usages: loadFixtureUsages(path.resolve("fixtures")), issues: [] };
  }

  const paths = buildPaths();
  const config = readConfig(paths);
  const monitored = new Set(config.providers);
  const usages: ProviderUsage[] = [];
  const issues: StatusIssue[] = [];

  if (monitored.has("codex")) {
    try {
      const codex = collectCodexUsage(path.join(paths.homeDir, ".codex"));
      saveUsage(codex, paths);
      usages.push(codex);
    } catch (error) {
      const cached = loadLatestUsage("codex", paths);
      if (cached) {
        usages.push(cached);
        issues.push({
          provider: "codex",
          severity: "warning",
          code: "CODEX_USING_CACHE",
          message: `Codex live usage unavailable; using cached usage from ${cached.observedAt}. ${error instanceof Error ? error.message : String(error)}`,
        });
      } else {
        issues.push({
          provider: "codex",
          severity: "error",
          code: "CODEX_USAGE_MISSING",
          message: error instanceof Error ? error.message : String(error),
        });
      }
    }
  }

  if (monitored.has("claude")) {
    const claude = loadLatestUsage("claude", paths);
    if (claude) {
      usages.push(claude);
    } else {
      issues.push({
        provider: "claude",
        severity: "warning",
        code: "CLAUDE_INGEST_MISSING",
        message: "Claude usage missing; add Burn AI ingest to Claude Code statusLine.command or remove claude from ~/.burn-ai/config.json providers.",
      });
    }
  }

  if (monitored.has("glm")) {
    const glmConfig = config.glm;
    if (!glmConfig?.apiKey) {
      issues.push({
        provider: "glm",
        severity: "warning",
        code: "GLM_API_KEY_MISSING",
        message: "GLM API key not configured. Edit ~/.burn-ai/config.json to set glm.apiKey.",
      });
    } else {
      try {
        const glm = await collectGlmUsage(glmConfig);
        saveUsage(glm, paths);
        usages.push(glm);
      } catch (error) {
        const cached = loadLatestUsage("glm", paths);
        if (cached) {
          usages.push(cached);
          issues.push({
            provider: "glm",
            severity: "warning",
            code: "GLM_USING_CACHE",
            message: `GLM live usage unavailable; using cached usage from ${cached.observedAt}. ${error instanceof Error ? error.message : String(error)}`,
          });
        } else {
          issues.push({
            provider: "glm",
            severity: "error",
            code: "GLM_USAGE_MISSING",
            message: error instanceof Error ? error.message : String(error),
          });
        }
      }
    }
  }

  return { usages, issues };
}

export async function collectLocalUsages(options: { fixtures?: boolean } = {}): Promise<ProviderUsage[]> {
  return (await collectLocalState(options)).usages;
}

export function analyzeUsages(usages: ProviderUsage[], profile: BurnProfile) {
  const paths = buildPaths();
  return createStatusSnapshot(usages, profile, { paths }).providers.map((provider) => provider.analysis);
}

export function readProfile(): BurnProfile {
  const profile = process.env.BURN_AI_PROFILE;
  return profile === "high" ? "high" : "low";
}

export function loadFixtureSamples(provider: string) {
  const file = path.resolve("fixtures", provider, "samples.jsonl");
  if (!fs.existsSync(file)) {
    return [];
  }
  return fs
    .readFileSync(file, "utf8")
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => JSON.parse(line) as ProviderUsage);
}

export function analyzeFixtureUsages(usages: ProviderUsage[], profile: BurnProfile): BurnAnalysis[] {
  const fixtureSamples = new Map<string, ProviderUsage[]>(
    usages.map((usage) => [usage.provider, loadFixtureSamples(usage.provider)]),
  );
  return createStatusSnapshot(usages, profile, { fixtureSamples }).providers.map((provider) => provider.analysis);
}

export async function collectStatusSnapshot(options: { fixtures?: boolean } = {}) {
  const { usages, issues } = await collectLocalState(options);
  const profile = readProfile();
  const fixtureSamples = options.fixtures
    ? new Map<string, ProviderUsage[]>(usages.map((usage) => [usage.provider, loadFixtureSamples(usage.provider)]))
    : undefined;
  const snapshot = createStatusSnapshot(usages, profile, { fixtureSamples, issues });
  if (!options.fixtures) {
    saveStatusSnapshot(snapshot);
  }
  return snapshot;
}

export function loadDisplayStatusSnapshot(
  options: { fixtures?: boolean; refresh?: boolean; paths?: RuntimePaths } = {},
): StatusSnapshot {
  if (options.fixtures || options.refresh) {
    throw new Error("Use collectStatusSnapshot() for async refresh; loadDisplayStatusSnapshot is sync (file-only)");
  }

  const snapshot = loadStatusSnapshot(options.paths);
  if (snapshot) {
    return refreshStatusSnapshotFreshness(snapshot);
  }

  return {
    generatedAt: new Date().toISOString(),
    profile: readProfile(),
    providers: [],
    issues: [
      {
        severity: "warning",
        code: "STATUS_MISSING",
        message: `No Burn AI status snapshot found at ${options.paths?.statusFile ?? buildPaths().statusFile}. Run burn-ai daemon --once or burn-ai status --refresh.`,
      },
    ],
  };
}
