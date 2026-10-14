"""Pydantic schemas for schema-constrained tender extraction.

These models are the contract the LLM output must satisfy before anything
reaches the database. The LLM's job is semantic understanding — reading
unstructured legal and technical prose and locating the facts. It is never
asked to judge whether a tender is worth bidding on.

Structure follows the PEGS framework (REPOMIX.md ADR-3):

    P — Participation : eligibility, mandatory prerequisites, certifications
    E — Evaluation    : how bids are scored
    G — Guidelines    : technical specification, BOM, quantities, delivery
    S — Selection     : award criteria and the decisive metric

Every field is Optional. A null is a correct answer for something the source
document does not state; a guess is a defect.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

# Kept content-agnostic on purpose: these are universal procurement concepts,
# not defense-specific ones (REPOMIX.md §1, dual-use architecture).


class Certification(BaseModel):
    """A standard, license, or clearance a bidder must hold."""

    name: str = Field(description="Exact name as written, e.g. 'AS9100D', 'ISO 27001'.")
    issuing_body: str | None = Field(
        default=None, description="Who issues it, if the document says."
    )
    mandatory: bool | None = Field(
        default=None,
        description="True if listed as a threshold condition, False if preferred/advantageous.",
    )


class ParticipationConditions(BaseModel):
    """PEGS — P. Who is allowed to bid at all."""

    eligibility_summary: str | None = Field(
        default=None, description="One paragraph describing who may participate."
    )
    mandatory_prerequisites: list[str] = Field(
        default_factory=list,
        description="Threshold conditions a bidder must meet. Verbatim where possible.",
    )
    required_certifications: list[Certification] = Field(default_factory=list)
    required_experience: str | None = Field(
        default=None,
        description="Past-performance requirements, e.g. 'three similar projects in five years'.",
    )
    financial_requirements: str | None = Field(
        default=None, description="Turnover thresholds, bonds, guarantees, insurance."
    )
    geographic_restrictions: str | None = Field(
        default=None, description="Nationality, registration, or local-content requirements."
    )


class EvaluationCriterion(BaseModel):
    """One scored line item in the evaluation matrix."""

    criterion: str
    weight_percent: float | None = Field(
        default=None,
        description="Weight as a percentage if stated numerically. Null if only described in prose.",
        ge=0,
        le=100,
    )
    description: str | None = None


class EvaluationFramework(BaseModel):
    """PEGS — E. How competing bids are scored against each other."""

    method: str | None = Field(
        default=None,
        description="e.g. 'lowest price', 'best value', 'quality/price 70:30'.",
    )
    criteria: list[EvaluationCriterion] = Field(default_factory=list)
    price_weight_percent: float | None = Field(default=None, ge=0, le=100)


class LineItem(BaseModel):
    """A single deliverable from the bill of materials or scope table."""

    description: str
    quantity: float | None = None
    unit: str | None = Field(
        default=None,
        description="Unit exactly as written ('each', 'kg', 'linear metre'). Never convert units.",
    )
    part_number: str | None = None


class TechnicalGuidelines(BaseModel):
    """PEGS — G. What is actually being bought."""

    scope_summary: str | None = Field(
        default=None, description="What the buyer is procuring, in two or three sentences."
    )
    technical_specs: list[str] = Field(
        default_factory=list,
        description="Discrete technical requirements, including referenced standards.",
    )
    referenced_standards: list[str] = Field(
        default_factory=list,
        description="Standard identifiers cited verbatim, e.g. 'MIL-STD-810H', 'EN 1090'.",
    )
    line_items: list[LineItem] = Field(default_factory=list)
    minimum_order_quantity: float | None = Field(
        default=None, description="MOQ if stated. Do not infer it from line item quantities."
    )
    delivery_terms: str | None = Field(
        default=None, description="Incoterms, delivery location, lead time commitments."
    )
    contract_duration: str | None = None
    warranty_terms: str | None = None


class SelectionMetrics(BaseModel):
    """PEGS — S. What ultimately decides the award."""

    award_basis: str | None = Field(
        default=None, description="The decisive rule, e.g. 'highest total score'."
    )
    decisive_metric: str | None = Field(
        default=None, description="The single metric with the most weight, if identifiable."
    )
    number_of_awards: int | None = Field(
        default=None, description="How many suppliers will be selected, if stated."
    )
    framework_agreement: bool | None = Field(
        default=None, description="True if this establishes a framework/IDIQ rather than one order."
    )


class KeyDate(BaseModel):
    """A milestone in the tender calendar other than the submission deadline."""

    label: str = Field(description="e.g. 'Q&A deadline', 'site visit', 'bid opening'.")
    date: datetime | None = None


class TenderExtraction(BaseModel):
    """The complete validated extraction for one tender.

    This is the object handed to `client.messages.parse(output_format=...)`.
    Nothing is written to `tenders` until an instance of this class exists.
    """

    # --- Universal header fields (map to real columns on `tenders`) ---
    title: str = Field(description="Official tender title, verbatim.")
    summary: str | None = Field(
        default=None, description="Two to four sentences: what is being bought and by whom."
    )
    buyer_name: str | None = Field(
        default=None, description="Contracting authority as named in the document."
    )
    reference_number: str | None = Field(
        default=None, description="The buyer's own tender/solicitation number."
    )
    publication_date: date | None = None
    deadline: datetime | None = Field(
        default=None, description="Submission deadline. Include time and timezone if stated."
    )
    estimated_value: float | None = Field(
        default=None, description="Numeric value only, no currency symbol or thousands separator."
    )
    currency: str | None = Field(default=None, description="ISO 4217 code, e.g. 'USD', 'ILS', 'EUR'.")
    language: str | None = Field(default=None, description="ISO 639-1 code of the source document.")

    # --- PEGS body (stored in tenders.metadata as JSONB) ---
    participation: ParticipationConditions = Field(default_factory=ParticipationConditions)
    evaluation: EvaluationFramework = Field(default_factory=EvaluationFramework)
    guidelines: TechnicalGuidelines = Field(default_factory=TechnicalGuidelines)
    selection: SelectionMetrics = Field(default_factory=SelectionMetrics)

    key_dates: list[KeyDate] = Field(default_factory=list)

    # --- Sector-specific overflow ---
    # The dual-use pressure valve. Anything true of this sector but not of
    # procurement generally goes here, so no schema change is needed to
    # support a new vertical (REPOMIX.md ADR-1).
    sector_specific: dict[str, object] = Field(
        default_factory=dict,
        description=(
            "Sector-specific facts that do not fit the fields above, as flat key/value pairs. "
            "For defense: security clearance level, ITAR/EAR classification, offset obligations. "
            "For healthcare: device class, regulatory pathway. Omit rather than invent."
        ),
    )

    # --- Self-reported extraction quality ---
    extraction_confidence: Literal["high", "medium", "low"] = Field(
        description=(
            "high: the document stated these facts explicitly. "
            "medium: some fields required interpretation. "
            "low: the document was fragmentary, badly OCR'd, or mostly boilerplate."
        )
    )
    unclear_sections: list[str] = Field(
        default_factory=list,
        description="Parts of the document that could not be read or understood confidently.",
    )

    def to_metadata(self) -> dict:
        """Serialize the PEGS body for the `tenders.metadata` JSONB column."""
        return {
            "participation": self.participation.model_dump(mode="json"),
            "evaluation": self.evaluation.model_dump(mode="json"),
            "guidelines": self.guidelines.model_dump(mode="json"),
            "selection": self.selection.model_dump(mode="json"),
            "key_dates": [d.model_dump(mode="json") for d in self.key_dates],
            "sector_specific": self.sector_specific,
            "reference_number": self.reference_number,
            "language": self.language,
            "extraction_confidence": self.extraction_confidence,
            "unclear_sections": self.unclear_sections,
        }

    def embedding_text(self) -> str:
        """Build the text that gets vectorized for semantic matching.

        Deliberately narrow: title, summary, scope, and technical requirements.
        Boilerplate legal conditions are near-identical across tenders from the
        same buyer and would pull every vector toward the same point.
        """
        parts: list[str] = [self.title]
        if self.summary:
            parts.append(self.summary)
        if self.guidelines.scope_summary:
            parts.append(self.guidelines.scope_summary)
        if self.guidelines.technical_specs:
            parts.append(" ".join(self.guidelines.technical_specs))
        if self.guidelines.referenced_standards:
            parts.append(" ".join(self.guidelines.referenced_standards))
        for item in self.guidelines.line_items:
            parts.append(item.description)
        return "\n".join(p for p in parts if p).strip()
