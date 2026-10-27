import uuid
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional, Literal
from datetime import datetime

# --- Ingestion Models ---

class PaperMetadata(BaseModel):
    title: Optional[str] = "No Title Found"
    authors: List[str] = []
    abstract: Optional[str] = "No Abstract Found"
    year: Optional[int] = None
    keywords: List[str] = []

class Paper(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    filename: str
    original_path: str
    content_markdown: str
    token_count: int
    metadata: PaperMetadata
    file_hash: Optional[str] = None
    status: str = "ready"
    timestamp: datetime = Field(default_factory=datetime.utcnow)

# --- Agent 1 (Extractor) Models ---

class EvidenceItem(BaseModel):
    quote: str
    page_reference: str = "N/A"
    relevance: str = ""
    verification_status: Literal["Verified", "Unverified"] = "Unverified"
    pdf_page_numbers: List[int] = Field(default_factory=list)
    verification_note: Optional[str] = None

class Extraction(BaseModel):
    paper_id: str
    criterion_id: str
    score: int
    score_justification: str
    evidence: List[EvidenceItem]
    strengths: List[str]
    weaknesses: List[str]
    confidence: float
    model_used: str
    extraction_timestamp: datetime = Field(default_factory=datetime.utcnow)
    cost: float = 0.0

# --- Agent 2 (Synthesizer) Models ---

class WeightedBreakdown(BaseModel):
    score: int
    weight: float
    weighted_score: float

class DetailedAssessment(BaseModel):
    major_strengths: List[str]
    major_concerns: List[str]
    minor_issues: List[str]

class Review(BaseModel):
    paper_id: str
    paper_title: str
    paper_filename: str
    overall_score: Optional[float] = None
    weighted_breakdown: Dict[str, WeightedBreakdown]
    recommendation: str
    recommendation_rationale: str
    executive_summary: str
    detailed_assessment: DetailedAssessment
    criterion_narrative: Dict[str, str]
    revision_suggestions: List[str]
    decision_confidence: float = Field(ge=0.0, le=1.0)
    flags: List[str] = []
    synthesis_timestamp: datetime = Field(default_factory=datetime.utcnow)
    extractor_model_used: str
    synthesizer_model_used: str
    total_cost: float = 0.0

    # Trust and coverage metadata. Defaults keep old saved progress readable.
    review_status: Literal["complete", "incomplete", "manual_review", "failed"] = "complete"
    provisional_recommendation: Optional[str] = None
    section_statuses: Dict[str, str] = Field(default_factory=dict)
    section_diagnostics: Dict[str, str] = Field(default_factory=dict)
    total_criteria: int = 0
    successful_criteria: int = 0
    failed_criteria: int = 0
    verified_evidence_count: int = 0
    unverified_evidence_count: int = 0
    criteria_with_insufficient_evidence: List[str] = Field(default_factory=list)
    criterion_errors: Dict[str, str] = Field(default_factory=dict)
    criterion_evidence: Dict[str, List[EvidenceItem]] = Field(default_factory=dict)
    confidence_method: str = "Heuristic reliability estimate; not statistically calibrated"
    reliability_components: Dict[str, float] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)

# --- Literature Grounding Models ---

class RelatedPaperMetadata(BaseModel):
    """Metadata for a paper found via literature search."""
    paper_id: str
    title: str
    authors: List[str]
    year: Optional[int] = None
    abstract: Optional[str] = None
    citation_count: Optional[int] = 0
    venue: Optional[str] = None
    url: Optional[str] = None
    open_access_pdf: Optional[str] = None
    key_findings: List[str] = []
    relevance_score: float = 0.0
    source: Optional[str] = None

class BaselineReference(BaseModel):
    """Output from Librarian agent - baseline literature for comparison."""
    sub_topic: str
    query_keywords: List[str]
    baseline_papers: List[RelatedPaperMetadata]
    key_findings_summary: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    total_api_calls: int = 0

class NoveltyRankedExtraction(Extraction):
    """Extended extraction with novelty assessment against baseline."""
    novelty_ranking: int = 3
    novelty_rationale: str = ""
    contradicts_baseline: bool = False
    extends_baseline: bool = False
    prior_work_gaps: List[str] = []

class FactCheckResult(BaseModel):
    """Result from Fact-Checker agent's verification search."""
    claim: str
    criterion_id: str
    search_query: str
    found_prior_work: bool = False
    prior_work_summary: str = ""
    verification_status: str = "pending"
    recommendation: str = ""
    papers_found: List[RelatedPaperMetadata] = []
    checked_at: datetime = Field(default_factory=datetime.utcnow)

class LiteratureContext(BaseModel):
    """All literature-related information for a review."""
    baseline_reference: Optional[BaselineReference] = None
    fact_checks: List[FactCheckResult] = []
    search_queries_made: List[str] = []
    total_api_calls: int = 0

class GroundedReview(Review):
    """Extended review with literature grounding."""
    literature_context: LiteratureContext = Field(default_factory=LiteratureContext)
    research_trajectory_section: str = ""
    base_score: Optional[float] = None
    novelty_adjusted_score: Optional[float] = None
    llm_fallback_used: bool = False
