import { makeProviderUsage, normalizeWindow } from "./usage.js";
import { MinimaxConfig, ProviderUsage } from "./types.js";

const CN_BASE_URL = "https://api.minimaxi.com";
const GLOBAL_BASE_URL = "https://api.minimax.io";

interface MinimaxModelRemain {
  model_name: string;
  start_time: number;
  end_time: number;
  remains_time: number;
  current_interval_total_count: number;
  current_interval_usage_count: number;
  current_weekly_total_count: number;
  current_weekly_usage_count: number;
  weekly_start_time: number;
  weekly_end_time: number;
  weekly_remains_time: number;
}

interface MinimaxQuotaResponse {
  model_remains: MinimaxModelRemain[];
  base_resp: {
    status_code: number;
    status_msg: string;
  };
}

function baseUrl(config: MinimaxConfig): string {
  return config.region === "global" ? GLOBAL_BASE_URL : CN_BASE_URL;
}

export async function fetchMinimaxQuota(config: MinimaxConfig): Promise<MinimaxQuotaResponse> {
  const url = `${baseUrl(config)}/v1/token_plan/remains`;

  const response = await fetch(url, {
    method: "GET",
    headers: {
      "Authorization": `Bearer ${config.apiKey}`,
      "Content-Type": "application/json",
    },
    signal: AbortSignal.timeout(15_000),
  });

  if (!response.ok) {
    throw new Error(`MiniMax quota API returned HTTP ${response.status}`);
  }

  const data = (await response.json()) as MinimaxQuotaResponse;

  if (data.base_resp?.status_code !== 0) {
    throw new Error(`MiniMax API error: ${data.base_resp?.status_msg ?? "unknown error"} (code ${data.base_resp?.status_code})`);
  }

  return data;
}

export function usageFromMinimaxQuota(
  quota: MinimaxQuotaResponse,
  options: { observedAt?: string; source: string },
): ProviderUsage | null {
  const models = quota.model_remains;
  if (!models || models.length === 0) {
    return null;
  }

  const primary = models[0];

  const fiveHourPercent = primary.current_interval_total_count > 0
    ? (primary.current_interval_usage_count / primary.current_interval_total_count) * 100
    : 0;

  const weeklyPercent = primary.current_weekly_total_count > 0
    ? (primary.current_weekly_usage_count / primary.current_weekly_total_count) * 100
    : 0;

  const fiveHour = normalizeWindow("five_hour", {
    used_percent: fiveHourPercent,
    resets_at: new Date(primary.end_time).toISOString(),
  }, 300);

  const sevenDay = primary.current_weekly_total_count > 0
    ? normalizeWindow("seven_day", {
      used_percent: weeklyPercent,
      resets_at: new Date(primary.weekly_end_time).toISOString(),
    }, 10080)
    : normalizeWindow("seven_day", {
      used_percent: fiveHourPercent,
      resets_at: new Date(primary.end_time).toISOString(),
    }, 10080);

  if (!fiveHour || !sevenDay) {
    return null;
  }

  return makeProviderUsage({
    provider: "minimax",
    source: options.source,
    observedAt: options.observedAt,
    planType: primary.model_name ?? null,
    fiveHour,
    sevenDay,
  });
}

export async function collectMinimaxUsage(config: MinimaxConfig): Promise<ProviderUsage> {
  if (!config.apiKey) {
    throw new Error("MiniMax API key not configured. Edit ~/.burn-ai/config.json to set minimax.apiKey.");
  }

  const quota = await fetchMinimaxQuota(config);
  const usage = usageFromMinimaxQuota(quota, {
    source: `${baseUrl(config)}/v1/token_plan/remains`,
  });

  if (!usage) {
    throw new Error("MiniMax quota response did not contain expected token plan data");
  }

  return usage;
}
