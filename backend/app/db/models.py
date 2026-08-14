"""Minimal persistence schema.

Jobs and product records are stored generically; the columns will be refined
once the real UniHack dataset shape is known.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class Job(Base):
    """A batch of products submitted for enrichment (lookup or Excel batch)."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), default="lookup")  # lookup | batch
    status: Mapped[str] = mapped_column(String(32), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    records: Mapped[list["ProductRecordModel"]] = relationship(back_populates="job")


class ProductRecordModel(Base):
    __tablename__ = "product_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    manufacturer: Mapped[str] = mapped_column(String(255), default="")
    brand: Mapped[str] = mapped_column(String(255), default="")
    part_number: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    # JSON payload: full EnrichmentResult (kept for backward compatibility).
    payload: Mapped[str] = mapped_column(Text, default="{}")

    # ----------------------------------------------------------------------
    # Step 10B persistent product-intelligence fields
    # ----------------------------------------------------------------------
    # These structured columns let the API reuse a stored product without
    # decoding the whole opaque ``payload`` JSON. They are populated by the
    # repository layer ONLY after a successful pipeline run; a failed run
    # never reaches them.
    #
    # Security: no API keys, Authorization headers, provider settings or
    # secrets are ever serialized into these fields.
    raw_description: Mapped[str] = mapped_column(Text, default="")
    sources_json: Mapped[str] = mapped_column(Text, default="[]")
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    attributes_json: Mapped[str] = mapped_column(Text, default="{}")
    descriptions_json: Mapped[str] = mapped_column(Text, default="{}")
    validation_json: Mapped[str] = mapped_column(Text, default="{}")
    enrichment_history_json: Mapped[str] = mapped_column(Text, default="[]")
    last_enriched_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    source_freshness_days: Mapped[int] = mapped_column(Integer, default=30)

    job: Mapped[Job] = relationship(back_populates="records")
