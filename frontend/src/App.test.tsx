import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { enrichOne, getHealth } from "./api/client";
import type { EnrichmentResult } from "./api/types";

vi.mock("./api/client", () => ({
  enrichOne: vi.fn(),
  getHealth: vi.fn(),
  getDashboard: vi.fn(),
  runBatch: vi.fn(),
  runEvaluation: vi.fn(),
}));

function emptyRequest(mpn: string) {
  return {
    Mfg_Part_Num: mpn,
    Part_Desc: "",
    E1_Brand: "",
    Unilog_Brand: "",
    DIB_Brand: "",
    Part_Manuf: "",
    source_url: "",
  };
}

function resultFor(mpn: string): EnrichmentResult {
  return {
    request: emptyRequest(mpn),
    input_row: {
      row_id: 1,
      mfg_part_num: mpn,
      part_desc: "",
      e1_brand: "",
      unilog_brand: "",
      dib_brand: "",
      part_manuf: "",
      mfg_part_num_value: mpn,
      part_desc_value: null,
      e1_brand_value: null,
      unilog_brand_value: null,
      dib_brand_value: null,
      part_manuf_value: null,
      missing_fields: [],
      mfg_part_num_duplicate: false,
      duplicate_group_id: null,
    },
    processing: {
      status: "needs_review",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      errors: [],
    },
    stages: [],
    current_stage: "delivery",
    discovery: {
      product: {
        manufacturer: "",
        brand: "",
        mpn,
        raw_description: "",
        sku: null,
      },
      candidates: [],
      rejected: [],
      total_discovered: 0,
      provider_errors: [],
    },
    evidence: [],
    extraction: null,
    validation: null,
    product: null,
    delivery: { values: [], notes: [], column_count: 0, headers: [] },
    review_reasons: [],
    quality: {
      overall: 0,
      evidence_coverage: 0,
      validation_coverage: 0,
      confidence: { count: 0, min: 0, max: 0, mean: 0 },
    },
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Single product P0 identity safety", () => {
  it("submits an MPN-only Quick Demo request after changing the demo MPN", async () => {
    vi.mocked(getHealth).mockResolvedValue({ status: "ok", app: "test", version: "1" });
    vi.mocked(enrichOne).mockImplementation(() => new Promise(() => {}));
    const user = userEvent.setup();
    render(<App />);

    const input = screen.getByPlaceholderText("e.g. XLC10ZW");
    await user.clear(input);
    await user.type(input, "1700-1PK-BB40");
    await user.click(screen.getByRole("button", { name: "Run enrichment" }));

    expect(enrichOne).toHaveBeenCalledWith(emptyRequest("1700-1PK-BB40"), {
      retrieveFromDb: false,
    });
  });

  it("clears an old result as soon as the MPN is edited", async () => {
    vi.mocked(getHealth).mockResolvedValue({ status: "ok", app: "test", version: "1" });
    vi.mocked(enrichOne).mockResolvedValue(resultFor("XLC10ZW"));
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Run enrichment" }));
    await screen.findByRole("heading", { name: /Result/ });

    const input = screen.getByPlaceholderText("e.g. XLC10ZW");
    await user.clear(input);
    await user.type(input, "1700-1PK-BB40");

    await waitFor(() => {
      expect(screen.queryByRole("heading", { name: /Result/ })).toBeNull();
    });
  });
});
