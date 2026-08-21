import { Component, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { enrichOne, getDashboard, getHealth, lookupMpn, runBatch, runEvaluation } from "./api/client";
import type { HealthResponse } from "./api/client";
import "./styles.css";
import type {
  BatchRequest,
  BatchResult,
  ComplianceSummary,
  DashboardResponse,
  EnrichmentRequest,
  EnrichmentResult,
  LookupResult,
  Severity,
  StageStatus,
  ValidationOutcome,
} from "./api/types";

class ErrorBoundary extends Component<
  { children: ReactNode },
  { error: string | null }
> {
  state = { error: null as string | null };
  static getDerivedStateFromError(err: unknown) {
    return { error: err instanceof Error ? err.message : String(err) };
  }
  render() {
    if (this.state.error) {
      return (
        <div className="error-box" role="alert">
          <strong>Something went wrong.</strong> {this.state.error}
          <br />
          <button className="secondary" onClick={() => this.setState({ error: null })}>
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

// Step 8B demo preset: a real manufacturer + MPN with an operator-confirmed
// manufacturer URL. This is an input (never copied from any dataset answer);
// the enrichment pipeline derives every attribute from discovered evidence.
const DEMO_REQUEST: EnrichmentRequest = {
  Mfg_Part_Num: "XLC10ZW",
  Part_Desc: "XLC10ZW Makita 18V Cordless Vacuum (Bare)",
  E1_Brand: "-- Unbranded --",
  Unilog_Brand: "-- No Unilog Brand --",
  DIB_Brand: "-- No DIB Brand --",
  Part_Manuf: "Makita Usa Inc (5142)",
  source_url: "https://makitatools.com/products/details/XLC10ZW",
};

const EMPTY_REQUEST: EnrichmentRequest = {
  Mfg_Part_Num: "",
  Part_Desc: "",
  E1_Brand: "",
  Unilog_Brand: "",
  DIB_Brand: "",
  Part_Manuf: "",
  source_url: "",
};

/** Quick mode deliberately sends no inherited demo metadata or source URL. */
export function quickMpnRequest(mpn: string): EnrichmentRequest {
  return { ...EMPTY_REQUEST, Mfg_Part_Num: mpn };
}

const FIELD_LABELS: { key: keyof EnrichmentRequest; label: string }[] = [
  { key: "Mfg_Part_Num", label: "Mfg Part Num" },
  { key: "Part_Desc", label: "Part Desc" },
  { key: "E1_Brand", label: "E1 Brand" },
  { key: "Unilog_Brand", label: "Unilog Brand" },
  { key: "DIB_Brand", label: "DIB Brand" },
  { key: "Part_Manuf", label: "Part Manuf" },
];

function isMpnOnlyInput(r: EnrichmentRequest): boolean {
  return (
    !r.Part_Desc &&
    !r.E1_Brand &&
    !r.Unilog_Brand &&
    !r.DIB_Brand &&
    !r.Part_Manuf &&
    !r.source_url
  );
}

const STAGE_COLORS: Record<StageStatus, string> = {
  pending: "#9ca3af",
  running: "#3b82f6",
  completed: "#16a34a",
  failed: "#dc2626",
  skipped: "#6b7280",
  needs_review: "#d97706",
};

const OUTCOME_COLORS: Record<ValidationOutcome, string> = {
  verified: "#16a34a",
  needs_review: "#d97706",
  not_validated: "#6b7280",
  invalid: "#dc2626",
};

const SEVERITY_COLORS: Record<Severity, string> = {
  info: "#2563eb",
  warning: "#d97706",
  error: "#dc2626",
};

const GREY = "#6b7280";

function Badge({ color, children }: { color: string; children: ReactNode }) {
  return (
    <span className="badge" style={{ background: color }}>
      {children}
    </span>
  );
}

function pct(value: number | undefined | null): string {
  if (value === undefined || value === null) return "–";
  return `${(value * 100).toFixed(0)}%`;
}

type BannerTone = "ok" | "warn" | "error" | "info";

interface Banner {
  tone: BannerTone;
  text: string;
}

function deriveBanners(result: EnrichmentResult): Banner[] {
  const banners: Banner[] = [];
  const { discovery, evidence, stages, processing, validation } = result;
  const stage = (name: string) => stages.find((s) => s.stage === name);

  const rejected = discovery.rejected.filter((c) => c.rejection_reason);
  if (rejected.length > 0) {
    banners.push({
      tone: "error",
      text: `Source rejected by policy: ${rejected[0].rejection_reason}`,
    });
  }
  if (discovery.total_discovered === 0) {
    banners.push({
      tone: "info",
      text: "No source found: supply a manufacturer-owned source URL, or configure a discovery provider.",
    });
  }
  const failedRecords = evidence.filter((e) => e.retrieval_status === "failed");
  if (failedRecords.length > 0) {
    banners.push({
      tone: "error",
      text: `Source retrieval failed for ${failedRecords.length} source(s): ${
        failedRecords[0].error_message ||
        failedRecords[0].error_kind ||
        "unknown error"
      }`,
    });
  }
  if (stage("extraction")?.status === "failed") {
    banners.push({
      tone: "error",
      text: `LLM unavailable: ${
        stage("extraction")?.note || "attribute extraction failed"
      }. Check the backend LLM configuration.`,
    });
  }
  const validationSkipped = stage("validation")?.status === "skipped";
  const hasValidatedAttributes =
    validation !== null && validation.attributes.length > 0;
  const notLoaded =
    hasValidatedAttributes &&
    validation.attributes.every((a) => a.outcome === "not_validated");
  if (validationSkipped || notLoaded) {
    banners.push({
      tone: "info",
      text: "Validation unavailable: the official UniHack LOV/UOM resources are not loaded, so attributes remain not-validated.",
    });
  }
  const descriptionStatus = stage("description")?.status;
  if (descriptionStatus === "failed") {
    banners.push({
      tone: "error",
      text: `Description generation unavailable: ${
        stage("description")?.note || "LLM call failed"
      }. Descriptions were left empty rather than fabricated.`,
    });
  } else if (descriptionStatus === "skipped") {
    banners.push({
      tone: "info",
      text: "No descriptions generated — there were no extracted attributes to describe.",
    });
  }
  if (processing.status === "completed") {
    banners.push({
      tone: "ok",
      text: "Successful enrichment: full 252-column delivery row produced.",
    });
  } else if (processing.status === "needs_review") {
    banners.push({
      tone: "warn",
      text: "Run finished with review items — see the review reasons below.",
    });
  } else if (processing.status === "failed") {
    banners.push({
      tone: "error",
      text: "The run failed — inspect the stage statuses below.",
    });
  }
  return banners;
}

const BANNER_STYLE: Record<BannerTone, string> = {
  ok: "#f0fdf4",
  warn: "#fffbeb",
  error: "#fef2f2",
  info: "#eff6ff",
};

const BANNER_BORDER: Record<BannerTone, string> = {
  ok: "#bbf7d0",
  warn: "#fde68a",
  error: "#fecaca",
  info: "#bfdbfe",
};

const BANNER_TEXT: Record<BannerTone, string> = {
  ok: "#166534",
  warn: "#92400e",
  error: "#b91c1c",
  info: "#1d4ed8",
};

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <details className="section">
      <summary>{title}</summary>
      <div className="section-body">{children}</div>
    </details>
  );
}

function Kv({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="kv">
      <span className="kv-key">{k}</span>
      <span className="kv-value">{v}</span>
    </div>
  );
}

function empty(text: string | null | undefined): string {
  return text && text.trim() ? text : "–";
}

type Tab = "single" | "database" | "batch";

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("single");

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch((err: unknown) =>
        setHealthError(err instanceof Error ? err.message : String(err))
      );
  }, []);

  return (
    <main className="app">
      <header className="header">
        <div>
          <h1>Product Truth Engine</h1>
          <p className="subtitle">
            Evidence-based product enrichment for the UniHack Delivery Format
          </p>
        </div>
        <div className="health">
          {healthError ? (
            <Badge color="#dc2626">backend unreachable</Badge>
          ) : health ? (
            <Badge color="#16a34a">
              backend {health.app} v{health.version}
              {typeof health.database_records === "number" && (
                <> · {health.database_records} records</>
              )}
            </Badge>
          ) : (
            <Badge color={GREY}>checking backend…</Badge>
          )}
        </div>
      </header>

      <nav className="tabs" role="tablist">
        {(
          [
            ["single", "Single product"],
            ["database", "Intelligence Store"],
            ["batch", "Batch"],
          ] as [Tab, string][]
        ).map(([name, label]) => (
          <button
            key={name}
            role="tab"
            aria-selected={tab === name}
            className={tab === name ? "active" : ""}
            onClick={() => setTab(name)}
          >
            {label}
          </button>
        ))}
      </nav>

      <ErrorBoundary key={tab}>
        {tab === "single" && <SingleProductTab />}
        {tab === "database" && <DatabaseTab />}
        {tab === "batch" && <BatchTab />}
      </ErrorBoundary>
    </main>
  );
}

function SingleProductTab() {
  const [quickMpn, setQuickMpn] = useState(DEMO_REQUEST.Mfg_Part_Num);
  const [advancedRequest, setAdvancedRequest] =
    useState<EnrichmentRequest>(EMPTY_REQUEST);
  const [result, setResult] = useState<EnrichmentResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [retrieveFromDb, setRetrieveFromDb] = useState(false);
  const [mode, setMode] = useState<"quick" | "advanced">("quick");
  const runId = useRef(0);

  function clearRunDisplay() {
    // A result is only valid for the exact form state that submitted it.
    setResult(null);
    setRunError(null);
  }

  function updateQuickMpn(value: string) {
    clearRunDisplay();
    setQuickMpn(value);
  }

  function updateAdvanced<K extends keyof EnrichmentRequest>(
    key: K,
    value: EnrichmentRequest[K]
  ) {
    clearRunDisplay();
    setAdvancedRequest((prev) => ({ ...prev, [key]: value }));
  }

  async function onRun() {
    const submittedRequest =
      mode === "quick" ? quickMpnRequest(quickMpn) : advancedRequest;
    const thisRun = ++runId.current;
    setLoading(true);
    clearRunDisplay();
    try {
      const nextResult = await enrichOne(submittedRequest, { retrieveFromDb });
      // Ignore a response from an invalidated run. Inputs are disabled while
      // loading, but this also protects against future UI changes/races.
      if (runId.current === thisRun) setResult(nextResult);
    } catch (err) {
      if (runId.current === thisRun) {
        setRunError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      if (runId.current === thisRun) setLoading(false);
    }
  }

  function downloadCsv() {
    if (!result) return;
    const csv = "\uFEFF" + toCsv(result.delivery.headers, result.delivery.values);
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${result.input_row.mfg_part_num_value || "product"}_delivery.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="panel">
      <nav className="tabs mode-toggle">
        <button
          type="button"
          className={mode === "quick" ? "active" : ""}
          disabled={loading}
          onClick={() => {
            clearRunDisplay();
            setMode("quick");
          }}
        >
          Quick Demo (MPN)
        </button>
        <button
          type="button"
          className={mode === "advanced" ? "active" : ""}
          disabled={loading}
          onClick={() => {
            clearRunDisplay();
            setMode("advanced");
          }}
        >
          Advanced / Official Input (6 fields)
        </button>
      </nav>

      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault();
          void onRun();
        }}
      >
        {mode === "quick" ? (
          <label className="field">
            <span>Mfg Part Num</span>
            <input
              value={quickMpn}
              placeholder="e.g. XLC10ZW"
              disabled={loading}
              onChange={(e) => updateQuickMpn(e.target.value)}
            />
          </label>
        ) : (
          <>
            {FIELD_LABELS.map(({ key, label }) => (
              <label className="field" key={key}>
                <span>{label}</span>
                <input
                  value={advancedRequest[key]}
                  disabled={loading}
                  onChange={(e) => updateAdvanced(key, e.target.value)}
                />
              </label>
            ))}
            <label className="field field-wide">
              <span>Manufacturer Source URL (optional)</span>
              <input
                value={advancedRequest.source_url ?? ""}
                disabled={loading}
                placeholder="https://makitatools.com/products/details/..."
                onChange={(e) => updateAdvanced("source_url", e.target.value)}
              />
            </label>
            <p className="field-hint">
              Must be a manufacturer-owned URL. Its hostname is used only as
              this request's manufacturer domain and still passes through the
              source policy — marketplaces (amazon.com, ebay.com, …) are always
              rejected.
            </p>
          </>
        )}
        <div className="form-actions">
          <label className="field-hint" style={{ display: "flex", alignItems: "center", gap: "0.4em" }}>
            <input
              type="checkbox"
              checked={retrieveFromDb}
              disabled={loading}
              onChange={(e) => setRetrieveFromDb(e.target.checked)}
            />
            Use stored result if fresh
          </label>
          <button
            type="button"
            className="secondary"
            disabled={loading}
            onClick={() => {
              clearRunDisplay();
              setAdvancedRequest(DEMO_REQUEST);
              setMode("advanced");
            }}
          >
            Load verified demo
          </button>
          <button type="submit" disabled={loading}>
            {loading && <span className="spinner" />}
            {loading ? "Running pipeline…" : "Run enrichment"}
          </button>
        </div>
      </form>

      {runError && <div className="error-box">Run failed: {runError}</div>}

      {result && isMpnOnlyInput(result.request) && result.processing.status === "needs_review" && (
        <div className="info-box">
          No identity or evidence could be resolved from the MPN alone. Open
          “Advanced / Official Input” and add Part_Desc, Part_Manuf, or a
          manufacturer source URL to enrich this product.
        </div>
      )}

      {result && result.__source__ === "database" && !result.__stale__ && (
        <div className="muted" style={{ marginBottom: "0.5em" }}>
          Loaded from Product Intelligence Store
        </div>
      )}
      {result && <Results result={result} onDownload={downloadCsv} />}
    </div>
  );
}

function DatabaseTab() {
  const [dashboard, setDashboard] = useState<DashboardResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [searchMpn, setSearchMpn] = useState("");
  const [searchResult, setSearchResult] = useState<LookupResult | null>(null);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);

  useEffect(() => {
    getDashboard()
      .then(setDashboard)
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : String(err))
      );
  }, []);

  async function onSearch() {
    const mpn = searchMpn.trim();
    if (!mpn) return;
    setSearching(true);
    setSearchError(null);
    setSearchResult(null);
    try {
      setSearchResult(await lookupMpn(mpn));
    } catch (err) {
      setSearchError(err instanceof Error ? err.message : String(err));
    } finally {
      setSearching(false);
    }
  }

  if (error) return <div className="error-box">Dashboard failed: {error}</div>;
  if (!dashboard) return <p className="muted panel">Loading…</p>;

  const db = dashboard.database;
  const lastRun = dashboard.last_batch_run;
  return (
    <div className="panel">
      <Section title="MPN Lookup">
        <form
          className="form"
          onSubmit={(e) => { e.preventDefault(); void onSearch(); }}
          style={{ display: "flex", gap: "0.5em", alignItems: "end" }}
        >
          <label className="field" style={{ flex: 1 }}>
            <span>Search by MPN</span>
            <input
              value={searchMpn}
              onChange={(e) => setSearchMpn(e.target.value)}
              placeholder="e.g. XLC10ZW"
            />
          </label>
          <button type="submit" disabled={searching} style={{ marginBottom: 2 }}>
            {searching ? "Searching…" : "Search"}
          </button>
        </form>
        {searchError && <div className="error-box">Lookup failed: {searchError}</div>}
        {searchResult && (
          <div style={{ marginTop: "0.75em" }}>
            <Kv k="MPN" v={searchResult.query} />
            <Kv k="Source" v={searchResult.source} />
            <Kv k="Freshness" v={
              searchResult.stale
                ? "STALE (data exists but may be outdated)"
                : "FRESH (recently enriched)"
            } />
            <Kv k="Total matches" v={searchResult.total_matches} />
            {searchResult.stored_records.map((rec, i) => (
              <div key={rec.record_id || i} className="candidate" style={{ marginTop: "0.5em" }}>
                <Kv k="Manufacturer" v={rec.manufacturer || "–"} />
                <Kv k="Brand" v={rec.brand || "–"} />
                <Kv k="Status" v={rec.status} />
                <Kv k="Last enriched" v={rec.last_enriched_at || "–"} />
              </div>
            ))}
          </div>
        )}
      </Section>

      <Section title="Product Intelligence Store">
        <Kv k="Total records" v={db.total_records} />
        <Kv k="Needs review" v={db.needs_review} />
        {Object.entries(db.by_status).length === 0 && (
          <p className="muted">No records stored yet.</p>
        )}
        {Object.entries(db.by_status).map(([status, count]) => (
          <Kv key={status} k={`Status: ${status}`} v={count} />
        ))}
        {db.recent_mpns.length > 0 && (
          <>
            <h4>Recent MPNs</h4>
            {db.recent_mpns.map((mpn) => (
              <div className="candidate" key={mpn}>
                <span className="muted">{mpn}</span>
              </div>
            ))}
          </>
        )}
      </Section>

      <Section title="Last batch run">
        {!lastRun && <p className="muted">No batch run recorded yet.</p>}
        {lastRun && (
          <>
            <Kv k="Job" v={`#${lastRun.job_id}`} />
            <Kv k="Status" v={lastRun.status} />
            <Kv k="Records" v={lastRun.record_count} />
            <Kv
              k="By status"
              v={
                Object.entries(lastRun.status_counts)
                  .map(([status, count]) => `${status}: ${count}`)
                  .join(" · ") || "–"
              }
            />
            <Kv k="Created" v={lastRun.created_at} />
          </>
        )}
      </Section>

      <ComplianceSection
        dashboard={dashboard}
        onRerun={() => {
          getDashboard()
            .then(setDashboard)
            .catch(() => {});
        }}
      />
    </div>
  );
}

function ComplianceSection({
  dashboard,
  onRerun,
}: {
  dashboard: DashboardResponse;
  onRerun: () => void;
}) {
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [token, setToken] = useState("");
  const [lastReport, setLastReport] = useState<string | null>(
    dashboard.compliance?.last_report_path ?? null
  );

  async function onRun() {
    setRunning(true);
    setError(null);
    setLastReport(null);
    try {
      const report = await runEvaluation({ live: false }, token || undefined);
      setLastReport(report.report_path);
      onRerun();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRunning(false);
    }
  }

  const compliance: ComplianceSummary | null = dashboard.compliance;
  const pct = (value: number | null) =>
    value == null ? "–" : `${(value * 100).toFixed(1)}%`;

  return (
    <Section title="P0 competition compliance">
      <p className="muted">
        Delivery-format rule enforcement. Run the offline evaluation harness
        to populate placeholder-leak and rule-pass metrics from the latest
        report.
      </p>
      <Kv
        k="Placeholder leak (derived cells)"
        v={compliance?.placeholder_leak_rows ?? "not run yet"}
      />
      <Kv k="Invoice rule pass rate" v={pct(compliance?.invoice_rule_pass_rate ?? null)} />
      <Kv k="Mobile rule pass rate" v={pct(compliance?.mobile_rule_pass_rate ?? null)} />
      <label className="field" style={{ maxWidth: 320 }}>
        <span>API Token</span>
        <input
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="EVALUATION_API_TOKEN"
        />
      </label>
      <div className="row-actions">
        <button onClick={onRun} disabled={running || !token.trim()}>
          {running ? "Running evaluation…" : "Run evaluation (offline)"}
        </button>
      </div>
      {error && <div className="error-box">Evaluation failed: {error}</div>}
      {lastReport && (
        <p className="muted">Last report: {lastReport}</p>
      )}
    </Section>
  );
}

function BatchTab() {
  const [mpns, setMpns] = useState("XLC10ZW, DCB518ASTS06G");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<BatchResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onRun() {
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const rows = mpns
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean)
        .map((mpn) => ({ Mfg_Part_Num: mpn }));
      if (rows.length === 0) {
        setError("Enter at least one MPN.");
        return;
      }
      const payload: BatchRequest = { rows };
      setResult(await runBatch(payload));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRunning(false);
    }
  }

  function download() {
    if (!result) return;
    const a = document.createElement("a");
    a.href = result.download_url;
    a.download = result.delivery_file;
    a.click();
  }

  const statusColor = (status: string) =>
    status === "completed"
      ? "#16a34a"
      : status === "failed"
        ? "#dc2626"
        : status === "needs_review"
          ? "#d97706"
          : "#6b7280";

  return (
    <div className="panel">
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault();
          void onRun();
        }}
      >
        <label className="field field-wide">
          <span>MPNs (comma-separated)</span>
          <input
            value={mpns}
            onChange={(e) => setMpns(e.target.value)}
            placeholder="XLC10ZW, DCB518ASTS06G"
            disabled={running}
          />
        </label>
        <p className="field-hint">
          Runs the real enrichment pipeline for each MPN you supply (as little
          as an MPN). Rows are never read from any bundled dataset — you
          provide the input.
        </p>
        <div className="form-actions">
          <button type="submit" disabled={running}>
            {running && <span className="spinner" />}
            {running ? "Running batch…" : "Run batch"}
          </button>
        </div>
      </form>

      {error && <div className="error-box">Batch failed: {error}</div>}

      {result && (
        <div className="results">
          <div className="results-header">
            <h2>Batch result</h2>
            {result.delivery_file && (
              <button onClick={download}>Download combined CSV</button>
            )}
          </div>
          <Kv k="Processed" v={`${result.processed} of ${result.requested}`} />
          <Kv
            k="By status"
            v={
              Object.entries(result.status_counts)
                .map(([status, n]) => `${status}: ${n}`)
                .join(" · ") || "–"
            }
          />
          {result.job_id !== null && <Kv k="Job" v={`#${result.job_id}`} />}
          {result.rows.length > 0 && (
            <>
              <h4>Rows</h4>
              {result.rows.map((row) => (
                <div className="candidate" key={row.row_id}>
                  <Badge color={statusColor(row.processing_status)}>
                    {row.processing_status}
                  </Badge>
                  <strong>{row.mfg_part_num || `row ${row.row_id}`}</strong>
                  <span className="muted">
                    {" "}
                    · {row.delivery_columns} delivery columns ·{" "}
                    {row.description_variants} description variant(s) ·{" "}
                    {row.review_reasons.length} review reason(s)
                  </span>
                  {row.review_reasons.length > 0 && (
                    <ul className="mini-reasons">
                      {row.review_reasons.slice(0, 3).map((reason, i) => (
                        <li key={i}>{reason}</li>
                      ))}
                      {row.review_reasons.length > 3 && (
                        <li className="muted">
                          …and {row.review_reasons.length - 3} more
                        </li>
                      )}
                    </ul>
                  )}
                </div>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function Results({
  result,
  onDownload,
}: {
  result: EnrichmentResult;
  onDownload: () => void;
}) {
  const stageStatus = result.processing.status;

  return (
    <div className="results">
      <div className="results-header">
        <h2>
          Result{" "}
          <Badge color={stageStatus === "completed" ? "#16a34a" : "#d97706"}>
            {stageStatus}
          </Badge>
        </h2>
        <button onClick={onDownload} disabled={!result.delivery.column_count}>
          Download delivery CSV
        </button>
      </div>

      {result.review_reasons.length > 0 && (
        <div className="review-reasons">
          <h3>Review reasons</h3>
          <ul>
            {result.review_reasons.map((reason, i) => (
              <li key={i}>{reason}</li>
            ))}
          </ul>
        </div>
      )}

      {deriveBanners(result).map((banner, i) => (
        <div
          key={i}
          className="banner"
          style={{
            background: BANNER_STYLE[banner.tone],
            borderColor: BANNER_BORDER[banner.tone],
            color: BANNER_TEXT[banner.tone],
          }}
        >
          {banner.text}
        </div>
      ))}

      <Section title={`Stages (${result.stages.length})`}>
        <ol className="stages">
          {result.stages.map((s) => (
            <li key={s.stage}>
              <Badge color={STAGE_COLORS[s.status]}>{s.status}</Badge>
              <strong>{s.stage}</strong>
              {s.note && <span className="muted"> — {s.note}</span>}
            </li>
          ))}
        </ol>
      </Section>

      <Section title="Input row">
        <Kv k="Mfg Part Num" v={result.input_row.mfg_part_num_value ?? "–"} />
        <Kv k="Part Desc" v={result.input_row.part_desc_value ?? "–"} />
        <Kv k="E1 Brand" v={result.input_row.e1_brand_value ?? "–"} />
        <Kv k="Unilog Brand" v={result.input_row.unilog_brand_value ?? "–"} />
        <Kv k="DIB Brand" v={result.input_row.dib_brand_value ?? "–"} />
        <Kv k="Part Manuf" v={result.input_row.part_manuf_value ?? "–"} />
        {result.input_row.missing_fields.length > 0 && (
          <Kv
            k="Missing fields"
            v={result.input_row.missing_fields.join(", ")}
          />
        )}
        {result.input_row.mfg_part_num_duplicate && (
          <Kv
            k="Duplicate"
            v={`part number duplicated (group ${result.input_row.duplicate_group_id ?? "?"})`}
          />
        )}
      </Section>

      <Section title="Discovery">
        <Kv
          k="Discovered"
          v={`${result.discovery.total_discovered} total, ${result.discovery.candidates.length} allowed, ${result.discovery.rejected.length} rejected, ${result.discovery.provider_errors.length} provider error(s)`}
        />
        {result.discovery.candidates.length > 0 && (
          <>
            <h4>Allowed candidates</h4>
            {result.discovery.candidates.map((c) => (
              <div className="candidate" key={c.id || c.url}>
                <a href={c.url} target="_blank" rel="noreferrer">
                  {c.title || c.url}
                </a>
                <span className="muted">
                  {" "}
                  ({c.domain}, {c.source_type}, score {pct(c.relevance_score)})
                </span>
              </div>
            ))}
          </>
        )}
        {result.discovery.rejected.length > 0 && (
          <>
            <h4>Rejected</h4>
            {result.discovery.rejected.map((c) => (
              <div className="candidate" key={c.id || c.url}>
                <span>
                  {c.title || c.url}{" "}
                  <span className="muted">
                    ({c.rejection_reason || c.status})
                  </span>
                </span>
              </div>
            ))}
          </>
        )}
        {result.discovery.provider_errors.length > 0 && (
          <>
            <h4>Provider errors</h4>
            {result.discovery.provider_errors.map((p, i) => (
              <div className="candidate" key={i}>
                <span className="error-text">
                  {p.provider_name} [{p.error_kind}]: {p.message}
                </span>
              </div>
            ))}
          </>
        )}
      </Section>

      <Section title={`Evidence (${result.evidence.length})`}>
        {result.evidence.length === 0 && (
          <p className="muted">No sources were retrieved.</p>
        )}
        {result.evidence.map((ev) => (
          <div className="evidence" key={ev.evidence_id}>
            <div className="evidence-head">
              <Badge color={ev.retrieval_status === "success" ? "#16a34a" : "#dc2626"}>
                {ev.retrieval_status}
              </Badge>
              <a href={ev.final_url || ev.url} target="_blank" rel="noreferrer">
                {ev.title || ev.url || "(no url)"}
              </a>
              <span className="muted">
                [{ev.evidence_id}] {ev.content_type}
              </span>
            </div>
            {ev.error_message && (
              <div className="error-text">
                {ev.error_kind ?? "error"}: {ev.error_message}
              </div>
            )}
            {ev.text && (
              <pre className="snippet">{ev.text.slice(0, 600)}</pre>
            )}
          </div>
        ))}
      </Section>

      <Section title={`Extraction (${result.extraction?.attributes.length ?? 0})`}>
        {!result.extraction && <p className="muted">No extraction ran.</p>}
        {result.extraction && (
          <>
            <Kv
              k="Accepted"
              v={`${result.extraction.attributes.length} attributes, ${result.extraction.rejected.length} rejected`}
            />
            {result.extraction.attributes.length > 0 && (
              <ExtractionList result={result} />
            )}
            {result.extraction.rejected.length > 0 && (
              <>
                <h4>Rejected claims</h4>
                {result.extraction.rejected.map((r, i) => (
                  <div className="candidate" key={i}>
                    <span className="error-text">
                      {r.name || "(unnamed)"}: {r.reason}
                    </span>
                  </div>
                ))}
              </>
            )}
          </>
        )}
      </Section>

      <Section title="Validation">
        {!result.validation && <p className="muted">No validation ran.</p>}
        {result.validation && (
          <>
            <Kv
              k="Outcomes"
              v={Object.entries(result.validation.counts)
                .map(([k, v]) => `${k}: ${v}`)
                .join(" · ")}
            />
            {result.validation.attributes.map((a, i) => (
              <div className="validated" key={i}>
                <div className="evidence-head">
                  <Badge color={OUTCOME_COLORS[a.outcome]}>{a.outcome}</Badge>
                  <strong>{a.name}</strong>
                  <span className="muted">
                    {" "}
                    raw={a.raw_value || "–"}
                    {a.normalized_value && ` → ${a.normalized_value}`}
                    {a.unit && ` ${a.unit}`} · conf {pct(a.confidence)}
                  </span>
                </div>
                {a.messages.map((m, j) => (
                  <div
                    key={j}
                    className="candidate"
                    style={{ color: SEVERITY_COLORS[m.severity] }}
                  >
                    [{m.source}] {m.message}
                  </div>
                ))}
              </div>
            ))}
          </>
        )}
      </Section>

      <Section title="Descriptions">
        <DescriptionsSection result={result} />
      </Section>

      <Section title="Product intelligence">
        {!result.product && <p className="muted">No product model produced.</p>}
        {result.product && (
          <>
            <Kv k="MPN" v={empty(result.product.identity.mpn)} />
            <Kv
              k="Manufacturer"
              v={empty(result.product.identity.manufacturer)}
            />
            <Kv
              k="Classification"
              v={
                [
                  result.product.classification.department,
                  result.product.classification.class,
                  result.product.classification.fine,
                ]
                  .filter(Boolean)
                  .join(" / ") || "–"
              }
            />
            {Object.values(result.product.attributes).map((attr) => (
              <div className="validated" key={attr.name}>
                <div className="evidence-head">
                  <Badge color={GREY}>{attr.status}</Badge>
                  <strong>{attr.name}</strong>
                  <span className="muted">
                    {" "}
                    = {attr.value || attr.raw_value || "–"}
                    {attr.unit && ` ${attr.unit}`} · {pct(attr.confidence)}
                    {attr.conflict_status !== "agreement" && (
                      <Badge color="#d97706"> {attr.conflict_status}</Badge>
                    )}
                  </span>
                </div>
                {attr.validation_results.map((vr, j) => (
                  <div className="candidate" key={j}>
                    <span className="muted">
                      [{vr.validation_type}] {vr.status}: {vr.message}
                    </span>
                  </div>
                ))}
              </div>
            ))}
            {result.product.processing.errors.map((err, i) => (
              <div className="candidate" key={i}>
                <span className="error-text">
                  {err.stage}: {err.message}
                </span>
              </div>
            ))}
            <h4>Quality</h4>
            <div className="quality-not-assessed">
              <strong>Overall quality: Not assessed</strong>
              <p>
                Official UniHack quality scoring requires the official
                validation/rule resources, which are not currently loaded.
              </p>
            </div>
            <Kv
              k="Evidence coverage"
              v={pct(result.product.quality.evidence_coverage)}
            />
            <Kv
              k="Validation coverage"
              v={pct(result.product.quality.validation_coverage)}
            />
            <Kv
              k="Confidence"
              v={`count ${result.product.quality.confidence.count}, min ${pct(
                result.product.quality.confidence.min
              )}, max ${pct(
                result.product.quality.confidence.max
              )}, mean ${pct(result.product.quality.confidence.mean)}`}
            />
          </>
        )}
      </Section>

      <Section title={`Delivery (${result.delivery.column_count} columns)`}>
        {result.delivery.notes.length > 0 && (
          <>
            <h4>Mapper notes</h4>
            {result.delivery.notes.map((n, i) => (
              <div className="candidate" key={i}>
                <span className="muted">{n}</span>
              </div>
            ))}
          </>
        )}
        {result.delivery.column_count > 0 ? (
          <>
            <h4>CSV preview</h4>
            <textarea
              readOnly
              rows={8}
              value={toCsv(result.delivery.headers, result.delivery.values)}
              spellCheck={false}
            />
            <button onClick={onDownload}>Download delivery CSV</button>
          </>
        ) : (
          <p className="muted">No delivery row was produced.</p>
        )}
      </Section>
    </div>
  );
}

function ExtractionList({ result }: { result: EnrichmentResult }) {
  if (!result.extraction) return null;
  const evidenceById = new Map(
    result.evidence.map((e) => [e.evidence_id, e] as const)
  );
  const outcomeByName = new Map(
    (result.validation?.attributes ?? []).map(
      (v) => [v.name, v.outcome] as const
    )
  );
  return (
    <>
      {result.extraction.attributes.map((a, i) => {
        const outcome = outcomeByName.get(a.name);
        const evidence = evidenceById.get(a.evidence_ids[0]);
        return (
          <div className="validated" key={i}>
            <div className="evidence-head">
              <strong>{empty(a.name)}</strong>
              <span className="muted">
                = {a.normalized_value || a.raw_value || "–"}
                {a.unit && ` ${a.unit}`} · conf {pct(a.confidence)}
              </span>
              {outcome ? (
                <Badge color={OUTCOME_COLORS[outcome]}>{outcome}</Badge>
              ) : (
                <Badge color={GREY}>not validated</Badge>
              )}
              {a.evidence_ids.map((id) => (
                <Badge key={id} color={GREY}>
                  {id}
                </Badge>
              ))}
            </div>
            <div className="candidate evidenceline">
              <span className="muted">Evidence: </span>
              {evidence ? (
                <a href={evidence.url} target="_blank" rel="noreferrer">
                  {evidence.url}
                </a>
              ) : (
                <span className="muted">(no source record)</span>
              )}
            </div>
            <div className="quote">
              {a.quote ? `"${a.quote}"` : "Evidence quote unavailable"}
            </div>
            {a.notes && (
              <div className="candidate muted">Note: {a.notes}</div>
            )}
          </div>
        );
      })}
    </>
  );
}

function DescriptionsSection({ result }: { result: EnrichmentResult }) {
  const descriptions = result.product?.descriptions;
  if (!descriptions) return <p className="muted">No descriptions generated.</p>;
  const filled = [
    ["Product title", descriptions.product_title],
    ["Short", descriptions.short_description],
    ["Mobile", descriptions.mobile_description],
    ["Invoice", descriptions.invoice_description],
    ["Long", descriptions.long_description],
    ["Retail", descriptions.retail_description],
    ["Marketing", descriptions.marketing_description],
    ["With", descriptions.with_],
    ["Application", descriptions.application],
    ["Includes", descriptions.includes],
    ["Product name", descriptions.product_name],
  ].filter((entry): entry is [string, string] => Boolean(entry[1]));

  if (filled.length === 0 && descriptions.item_features.length === 0) {
    return <p className="muted">No description variants were produced.</p>;
  }
  return (
    <>
      {filled.map(([label, value]) => (
        <Kv key={label} k={label} v={value} />
      ))}
      {descriptions.item_features.length > 0 && (
        <>
          <h4>Item features</h4>
          {descriptions.item_features.map((feature, i) => (
            <div className="candidate" key={i}>
              <span className="muted">•</span> {feature}
            </div>
          ))}
        </>
      )}
    </>
  );
}

function toCsv(headers: string[], values: string[]): string {
  // Spreadsheet formula-injection guard (Step 9B): mirrors the backend
  // writer policy so single-product downloads are equally safe. "=", "+" and
  // "@" prefixes are always escaped; "-" only when it starts something that
  // could parse as an expression (negative numbers, the "-" placeholder and
  // hyphenated part numbers pass through verbatim).
  const safe = (v: string) => {
    if (!v) return v;
    const first = v[0];
    if (first === "=" || first === "+" || first === "@") return "'" + v;
    if (
      first === "-" &&
      v.length > 1 &&
      v[1] !== "-" &&
      !/[\d.]/.test(v[1])
    ) {
      return "'" + v;
    }
    return v;
  };
  const esc = (v: string) => {
    const value = safe(v);
    return /[",\r\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
  };
  return [headers.map(esc).join(","), values.map(esc).join(",")].join("\r\n");
}
