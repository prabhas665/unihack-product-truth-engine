import type {
  BatchRequest,
  BatchResult,
  DashboardResponse,
  EnrichmentRequest,
  EnrichmentResult,
  LookupResult,
} from "./types";

export interface HealthResponse {
  status: string;
  app: string;
  version: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`API error ${res.status}: ${detail || res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/api/health");
}

export function enrichOne(
  payload: EnrichmentRequest,
  options?: { retrieveFromDb?: boolean }
): Promise<EnrichmentResult> {
  const params = options?.retrieveFromDb ? "?retrieve_from_db=true" : "";
  return request<EnrichmentResult>(`/api/enrich${params}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function lookupMpn(mpn: string): Promise<LookupResult> {
  return request<LookupResult>(
    `/api/lookup?mpn=${encodeURIComponent(mpn)}`
  );
}

export function getDashboard(): Promise<DashboardResponse> {
  return request<DashboardResponse>("/api/dashboard");
}

export interface EvaluationRequest {
  input_path?: string;
  expected_path?: string;
  live?: boolean;
  limit?: number | null;
  out_dir?: string;
}

export interface EvaluationReport {
  generated_at: string;
  mode: string;
  rows_total: number;
  rows_evaluated: number;
  identity_exact_matches: number;
  identity_exact_match_rate: number;
  placeholder_leak_rows: number;
  placeholder_leak_count: number;
  invoice_rule_total: number;
  invoice_rule_passed: number;
  mobile_rule_total: number;
  mobile_rule_passed: number;
  invoice_length_histogram: Record<string, number>;
  benchmark: Array<{
    mpn: string;
    exact_match: boolean;
    leak: boolean;
    comparisons: Array<{
      column: string;
      expected: string;
      actual: string;
      match: boolean | null;
    }>;
  }>;
  report_path: string;
}

export function runEvaluation(
  payload: EvaluationRequest = {}
): Promise<EvaluationReport> {
  return request<EvaluationReport>("/api/evaluation/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function runBatch(payload: BatchRequest): Promise<BatchResult> {
  return request<BatchResult>("/api/batch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}
