// TypeScript mirrors of the backend pydantic models returned by /api/enrich.

export interface EnrichmentRequest {
  Mfg_Part_Num: string;
  Part_Desc: string;
  E1_Brand: string;
  Unilog_Brand: string;
  DIB_Brand: string;
  Part_Manuf: string;
  source_url?: string;
}

// --- pipeline / stages ---

export type StageName =
  | "input"
  | "discovery"
  | "retrieval"
  | "extraction"
  | "validation"
  | "description"
  | "product_intelligence"
  | "delivery";

export type StageStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "skipped"
  | "needs_review";

export interface StageState {
  stage: StageName;
  status: StageStatus;
  note: string;
}

export type ProcessingStatus =
  | "pending"
  | "in_progress"
  | "needs_review"
  | "completed"
  | "failed";

export interface ProcessingError {
  stage: string;
  message: string;
  occurred_at: string;
  retryable: boolean;
}

export interface ProcessingMetadata {
  status: ProcessingStatus;
  created_at: string;
  updated_at: string;
  errors: ProcessingError[];
}

// --- identity ---

export interface ProductIdentity {
  manufacturer: string;
  brand: string;
  mpn: string;
  raw_description: string;
  sku: string | null;
}

export interface InputRowView {
  row_id: number;
  mfg_part_num: string;
  part_desc: string;
  e1_brand: string;
  unilog_brand: string;
  dib_brand: string;
  part_manuf: string;
  mfg_part_num_value: string | null;
  part_desc_value: string | null;
  e1_brand_value: string | null;
  unilog_brand_value: string | null;
  dib_brand_value: string | null;
  part_manuf_value: string | null;
  missing_fields: string[];
  mfg_part_num_duplicate: boolean;
  duplicate_group_id: string | null;
}

// --- discovery ---

export type SourceType =
  | "manufacturer_product_page"
  | "manufacturer_technical_pdf"
  | "manufacturer_manual"
  | "manufacturer_catalogue"
  | "manufacturer_digital_asset"
  | "unknown";

export type CandidateStatus = "pending" | "allowed" | "prohibited" | "rejected";
export type ManufacturerRelationship = "owned" | "external" | "unknown";
export type DiscoveryMethod = "search" | "direct_url" | "document" | "manual";

export interface SourceCandidate {
  id: string;
  url: string;
  source_type: SourceType;
  title: string;
  domain: string;
  manufacturer_relationship: ManufacturerRelationship;
  trust_level: string;
  relevance_score: number;
  discovery_method: DiscoveryMethod;
  status: CandidateStatus;
  rejection_reason: string;
}

export interface ProviderErrorInfo {
  provider_name: string;
  error_kind: string;
  message: string;
}

export interface DiscoveryResult {
  product: ProductIdentity;
  candidates: SourceCandidate[];
  rejected: SourceCandidate[];
  total_discovered: number;
  provider_errors: ProviderErrorInfo[];
}

// --- evidence retrieval ---

export type RetrievalStatus = "success" | "failed" | "skipped";
export type ExtractionStatus = "extracted" | "partial" | "failed" | "not_applicable";

export interface EvidenceRecord {
  evidence_id: string;
  source_candidate_id: string;
  url: string;
  final_url: string;
  source_type: SourceType;
  title: string;
  text: string;
  content_type: string;
  retrieved_at: string;
  retrieval_status: RetrievalStatus;
  extraction_status: ExtractionStatus;
  error_kind: string | null;
  error_message: string;
  metadata: Record<string, string>;
}

// --- extraction ---

export interface CandidateAttribute {
  name: string;
  raw_value: string;
  normalized_value: string;
  unit: string;
  confidence: number;
  evidence_ids: string[];
  notes: string;
  quote: string;
}

export interface RejectedAttribute {
  name: string;
  raw_value: string;
  reason: string;
}

export interface ExtractionResponse {
  attributes: CandidateAttribute[];
  rejected: RejectedAttribute[];
  evidence_ids_used: string[];
}

// --- validation ---

export type ValidationOutcome =
  | "verified"
  | "needs_review"
  | "not_validated"
  | "invalid";

export type Severity = "info" | "warning" | "error";

export interface ValidationMessage {
  code: string;
  severity: Severity;
  source: string;
  message: string;
}

export interface ValidatedAttribute {
  name: string;
  raw_value: string;
  normalized_value: string;
  unit: string;
  confidence: number;
  evidence_refs: string[];
  outcome: ValidationOutcome;
  messages: ValidationMessage[];
  normalization_applied: string[];
}

export interface ValidationSummary {
  attributes: ValidatedAttribute[];
  counts: Record<string, number>;
}

// --- product intelligence (domain model) ---

export type AttributeStatus =
  | "extracted"
  | "normalized"
  | "validated"
  | "needs_review"
  | "rejected";

export type ConflictStatus = "agreement" | "conflict" | "unresolved";

export interface ReviewState {
  needs_review: boolean;
  reason: string;
  decision: string | null;
  reviewer_notes: string;
  reviewed_by: string | null;
  reviewed_at: string | null;
}

export interface CandidateValue {
  value: string;
  normalized_value: string;
  unit: string;
  confidence: number;
  evidence_refs: string[];
  source_trust_level: string;
}

export interface DomainValidationResult {
  validation_type: string;
  status: string;
  message: string;
  details: Record<string, unknown>;
}

export interface AttributeValue {
  name: string;
  raw_value: string;
  value: string;
  unit: string;
  confidence: number;
  status: AttributeStatus;
  evidence_refs: string[];
  validation_results: DomainValidationResult[];
  candidates: CandidateValue[];
  conflict_status: ConflictStatus;
  review: ReviewState | null;
}

export interface DomainEvidence {
  id: string;
  source_url: string;
  source_type: SourceType;
  source_title: string;
  snippet: string;
  retrieved_at: string;
  trust_level: string;
  supports_attributes: string[];
  assets: Record<string, string>;
}

export interface Classification {
  department: string;
  class: string;
  fine: string;
  classpath: string;
  product_type: string;
}

export interface Descriptions {
  product_title: string;
  short_description: string;
  mobile_description: string;
  invoice_description: string;
  long_description: string;
  retail_description: string;
  marketing_description: string;
  item_features: string[];
  with_: string;
  application: string;
  includes: string;
  product_name: string;
}

export interface ConfidenceSummary {
  count: number;
  min: number;
  max: number;
  mean: number;
}

export interface QualityScore {
  overall: number;
  evidence_coverage: number;
  validation_coverage: number;
  confidence: ConfidenceSummary;
}

export interface ProductIntelligence {
  identity: ProductIdentity;
  classification: Classification;
  attributes: Record<string, AttributeValue>;
  evidence: Record<string, DomainEvidence>;
  descriptions: Descriptions;
  quality: QualityScore;
  processing: ProcessingMetadata;
}

// --- delivery ---

export interface DeliveryRowView {
  values: string[];
  notes: string[];
  column_count: number;
  headers: string[];
}

// --- full result ---

export interface EnrichmentResult {
  request: EnrichmentRequest;
  input_row: InputRowView;
  processing: ProcessingMetadata;
  stages: StageState[];
  current_stage: StageName;
  discovery: DiscoveryResult;
  evidence: EvidenceRecord[];
  extraction: ExtractionResponse | null;
  validation: ValidationSummary | null;
  product: ProductIntelligence | null;
  delivery: DeliveryRowView;
  review_reasons: string[];
  quality: QualityScore;
  // Step 10B: present when returned from DB-hit path
  __source__?: string;
  __stale__?: boolean;
  __record_id__?: number;
  __last_enriched_at__?: string | null;
}

// --- lookup / dashboard / batch (Step 9) ---

export interface StoredRecordView {
  record_id: number;
  part_number: string;
  manufacturer: string;
  brand: string;
  description: string;
  status: string;
  last_enriched_at: string | null;
  source_freshness_days: number;
}

export interface LookupResult {
  query: string;
  total_matches: number;
  source: string;
  stale: boolean;
  rows: InputRowView[];
  stored_records: StoredRecordView[];
}

export interface DatabaseStats {
  total_records: number;
  by_status: Record<string, number>;
  needs_review: number;
  recent_mpns: string[];
}

export interface BatchRunSummary {
  job_id: number;
  created_at: string;
  status: string;
  record_count: number;
  status_counts: Record<string, number>;
}

export interface ComplianceSummary {
  placeholder_leak_rows: number | null;
  invoice_rule_pass_rate: number | null;
  mobile_rule_pass_rate: number | null;
  last_report_path: string | null;
}

export interface DashboardResponse {
  database: DatabaseStats;
  last_batch_run: BatchRunSummary | null;
  compliance: ComplianceSummary | null;
}

export interface BatchInputRow {
  Mfg_Part_Num: string;
  Part_Desc?: string;
  E1_Brand?: string;
  Unilog_Brand?: string;
  DIB_Brand?: string;
  Part_Manuf?: string;
}

export interface BatchRequest {
  rows: BatchInputRow[];
  start?: number;
  limit?: number;
  mpns?: string[];
}

export interface BatchRowResult {
  row_id: number;
  mfg_part_num: string;
  processing_status: string;
  delivery_columns: number;
  review_reasons: string[];
  description_variants: number;
}

export interface BatchResult {
  requested: number;
  processed: number;
  status_counts: Record<string, number>;
  rows: BatchRowResult[];
  delivery_file: string;
  download_url: string;
  job_id: number | null;
}
