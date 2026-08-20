"""Prompt builder for evidence-based attribute extraction.

The prompt is the hallucination guard: it instructs the model to use ONLY
the supplied evidence, to cite evidence ids for every claim, and to preserve
uncertainty instead of guessing.
"""

from __future__ import annotations

from app.extraction.types import ExtractionRequest

SYSTEM_PROMPT = (
    "You are a careful product data extractor. Your ONLY source of truth is "
    "the evidence provided by the user. You have no other knowledge of this "
    "product."
)


def build_extraction_prompt(
    request: ExtractionRequest, max_chars_per_record: int = 6000
) -> str:
    lines = [
        "Extract product attributes from the SUPPLIED EVIDENCE ONLY.",
        "Rules:",
        "- Use ONLY the evidence below. Never use knowledge from outside it.",
        "- Do not guess and do not infer specifications the evidence does not state.",
        "- Never fabricate a value: every attribute MUST list the evidence_ids that support it.",
        "- evidence_ids MUST reference ONLY the bracketed [id] sections in the Evidence block.",
        "- The 'Requested product context' block below is NOT evidence: never cite it as an evidence_id.",
        "- If the evidence conflicts for an attribute, emit ONE candidate per supported value (same name, different evidence_ids).",
        "- If a value is uncertain, lower its confidence instead of hiding the uncertainty.",
        "- Extract EVERY attribute the evidence states, not just a few: do not stop after the first handful.",
        "- Respond in English: translate non-English evidence values into English; write notes in English.",
        "- normalized_value: fill ONLY when normalization is obvious and evidence-supported (e.g. '100 mm' -> '100' with unit 'mm'); otherwise leave empty.",
        "- notes: one short, evidence-based, user-safe line. Never include private reasoning or chain-of-thought.",
        "",
        "Output format (MANDATORY):",
        "- Respond with a SINGLE JSON object matching the requested schema: "
        '{"items": [{"name": ..., "raw_value": ..., "normalized_value": ..., '
        '"unit": ..., "confidence": ..., "evidence_ids": [...], "notes": ...}]}.',
        "- No markdown fences, no bullet lists, no commentary before or after the JSON.",
        "",
        "Requested product context (NOT evidence):",
    ]
    identity = request.identity
    if identity.manufacturer:
        lines.append(f"- manufacturer: {identity.manufacturer}")
    if identity.brand:
        lines.append(f"- brand: {identity.brand}")
    if identity.mpn:
        lines.append(f"- mpn: {identity.mpn}")
    description = request.raw_description or identity.raw_description
    if description:
        lines.append(f"- raw description: {description}")

    lines.append("")
    lines.append("Evidence:")
    for record in request.evidence_records:
        text = record.text or record.title
        if len(text) > max_chars_per_record:
            text = text[:max_chars_per_record] + " ...(truncated)"
        header = f"[{record.evidence_id}]"
        if record.title:
            header += f" {record.title}"
        header += f" | {record.url}"
        lines.append(header)
        lines.append(text)
        lines.append("")
    return "\n".join(lines)
