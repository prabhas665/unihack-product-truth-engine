"""Evidence-bound, LLM-powered description generation.

Generates the commerce-ready description variants (title, short, mobile,
invoice, long, retail, marketing, features, With/Application/Includes,
Product Name) for a product from KNOWN facts only: the attributes that
survived extraction (each with its evidence), plus direct evidence quotes.

The same honesty rules as extraction apply:

- The model has no knowledge outside the supplied facts - the system prompt
  forbids inventing specifications, features, or claims.
- The output is validated against the ``GeneratedDescriptions`` schema; a
  malformed provider response raises a typed LLM error and the pipeline
  surfaces it as a FAILED stage instead of fabricating copy.
- Every variant defaults to empty; nothing is filled with guessed text.

All LLM failures surface as the typed errors from app.llm (configuration,
provider unavailable, timeout, invalid response) so the pipeline can record
review reasons without ever crashing the run.
"""

from __future__ import annotations

from pydantic import ValidationError

from app.core.domain import AttributeValue, Descriptions, ProductIdentity
from app.llm import LLMClient, LLMInvalidResponseError, StructuredCompletionRequest
from app.descriptions.types import GeneratedDescriptions

SYSTEM_PROMPT = (
    "You are a technical copywriter for an industrial product catalog. "
    "Write commerce-ready product descriptions using ONLY the supplied "
    "attributes and evidence quotes. You have no knowledge outside those "
    "facts: never invent specifications, part numbers, features, materials, "
    "compliance claims, or marketing superlatives. Every sentence must be "
    "traceable to a supplied fact. Keep each variant concise and truthful."
)

# Fields that semantically carry ITEM LISTS (the delivery "With",
# "Application" and "Includes" columns): a provider list[str] is joined
# deterministically, never rewritten or extended.
_ITEM_FIELDS = frozenset({"with", "application", "includes"})
# Deterministic separator for joined itemized fields.
_FIELD_JOIN_SEPARATOR = "; "
# Prose fields that must stay single strings.
_PLAIN_STRING_FIELDS = frozenset(
    {
        "product_title",
        "short_description",
        "mobile_description",
        "invoice_description",
        "long_description",
        "retail_description",
        "marketing_description",
        "product_name",
    }
)
_ALL_FIELDS = frozenset(_ITEM_FIELDS | _PLAIN_STRING_FIELDS | {"item_features"})


def _normalize_generated_descriptions(
    raw: dict,
) -> tuple[dict, list[str]]:
    """Per-field normalize a partially schema-invalid description payload.

    Returns (normalized, reasons). Strings are preserved verbatim; None
    becomes ""; the itemized fields with/application/includes accept a
    list[str] that is joined with ``_FIELD_JOIN_SEPARATOR`` (blank items
    dropped, order preserved); item_features keeps its string entries in
    order and drops non-string ones; any other type on any field blanks
    ONLY that field. Nothing is fabricated and unknown extra keys are
    ignored.
    """
    normalized: dict = {}
    reasons: list[str] = []
    for key, value in raw.items():
        if key not in _ALL_FIELDS:
            continue
        if key == "item_features":
            if value is None:
                normalized[key] = []
            elif isinstance(value, list):
                kept: list[str] = []
                for element in value:
                    if isinstance(element, str):
                        kept.append(element)
                    else:
                        reasons.append(
                            f"description field 'item_features' dropped a "
                            f"non-string entry ({type(element).__name__})"
                        )
                normalized[key] = kept
            else:
                reasons.append(
                    f"description field 'item_features' had unsupported "
                    f"value type {type(value).__name__} and was left blank"
                )
                normalized[key] = []
            continue
        if value is None:
            normalized[key] = ""
        elif isinstance(value, str):
            normalized[key] = value
        elif key in _ITEM_FIELDS and isinstance(value, list) and all(
            isinstance(element, str) for element in value
        ):
            items = [item for item in value if item.strip()]
            normalized[key] = _FIELD_JOIN_SEPARATOR.join(items)
        else:
            reasons.append(
                f"description field '{key}' had unsupported value type "
                f"{type(value).__name__} and was left blank"
            )
            normalized[key] = ""
    return normalized, reasons

_PROMPT_TEMPLATE = """Generate the full set of product descriptions for the product below.

PRODUCT IDENTITY
- Manufacturer part number: {mpn}
- Manufacturer: {manufacturer}
- Input description: {raw_description}

KNOWN ATTRIBUTES (each carries its confidence; treat low-confidence values as uncertain and omit them)
{attributes}

EVIDENCE QUOTES (verbatim excerpts from the manufacturer sources)
{quotes}

Return JSON with EXACTLY these keys:
product_title, short_description, mobile_description, invoice_description,
long_description, retail_description, marketing_description, item_features
(array of strings), with, application, includes, product_name.

Rules:
- Use only the facts above; never add anything not present in the attributes
  or quotes.
- If a fact is missing, leave that field empty ("" or []).
- item_features: one concise feature per entry, each grounded in a fact.
- The "with" field lists accessories/items included with the product; leave
  it empty unless the facts say so."""


def build_prompt(
    identity: ProductIdentity,
    attributes: dict[str, AttributeValue],
    quotes: list[str],
) -> str:
    """Deterministic prompt builder; uses known facts only."""
    if not attributes:
        raise ValueError("no attributes to describe")

    lines = []
    for name in sorted(attributes):
        attribute = attributes[name]
        value = attribute.value or attribute.raw_value
        if not value:
            continue
        unit = f" {attribute.unit}" if attribute.unit else ""
        lines.append(
            f"- {name}: {value}{unit} (confidence {attribute.confidence:.0%})"
        )
    attribute_block = "\n".join(lines) or "(none)"
    quote_block = "\n".join(f"> {quote}" for quote in quotes) or "(none)"

    return _PROMPT_TEMPLATE.format(
        mpn=identity.mpn or "(unknown)",
        manufacturer=identity.manufacturer or "(unknown)",
        raw_description=identity.raw_description or "(unknown)",
        attributes=attribute_block,
        quotes=quote_block,
    )


class DescriptionsService:
    """Generates description variants through the injected LLM client."""

    def __init__(self, client: LLMClient) -> None:
        self._client = client

    def generate(
        self,
        *,
        identity: ProductIdentity,
        attributes: dict[str, AttributeValue],
        quotes: list[str] | None = None,
        out_reasons: list[str] | None = None,
        timeout_seconds: float | None = None,
    ) -> Descriptions:
        """Generate all variants; raises the typed LLM errors on failure.

        A partially schema-invalid response (one malformed field among valid
        ones) is salvaged per field by ``_normalize_generated_descriptions``:
        valid variants are preserved verbatim and only the malformed field is
        blanked, with a reason appended to ``out_reasons`` (when given). An
        unusable whole response (not JSON, not a dict) still raises, exactly
        as before; nothing is ever fabricated.
        """
        try:
            result = self._client.structured_completion(
                StructuredCompletionRequest(
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=build_prompt(
                        identity, attributes, quotes or []
                    ),
                    output_schema=GeneratedDescriptions,
                    timeout_seconds=timeout_seconds,
                )
            )
        except LLMInvalidResponseError as exc:
            if not isinstance(exc.raw, dict):
                raise
            normalized, reasons = _normalize_generated_descriptions(exc.raw)
            try:
                result = GeneratedDescriptions.model_validate(normalized)
            except ValidationError:
                # Normalization only ever produces valid types; if this
                # still fails, fail safely with the original error.
                raise exc from None
            if out_reasons is not None:
                out_reasons.extend(reasons)
        assert isinstance(result, GeneratedDescriptions)
        return Descriptions(
            product_title=result.product_title,
            short_description=result.short_description,
            mobile_description=result.mobile_description,
            invoice_description=result.invoice_description,
            long_description=result.long_description,
            retail_description=result.retail_description,
            marketing_description=result.marketing_description,
            item_features=list(result.item_features),
            with_=result.with_,
            application=result.application,
            includes=result.includes,
            product_name=result.product_name,
        )

    def evidence_quotes(self, evidence_texts: list[str], limit: int = 3) -> list[str]:
        """Short verbatim excerpts from the retrieved evidence (for grounding).

        Passed alongside the attributes so the model has direct quotes to
        work from; quotes are truncated, never invented.
        """
        quotes: list[str] = []
        for text in evidence_texts:
            cleaned = " ".join(text.split())
            if not cleaned:
                continue
            quotes.append(cleaned[:280])
            if len(quotes) >= limit:
                break
        return quotes
