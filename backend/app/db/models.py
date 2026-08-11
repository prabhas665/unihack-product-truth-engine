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
    # JSON payload: attributes, evidence, conflicts (see app.core.domain).
    payload: Mapped[str] = mapped_column(Text, default="{}")

    job: Mapped[Job] = relationship(back_populates="records")
