from core.data_models import PaperMetadata, EvidenceItem, Extraction


def test_paper_metadata_defaults():
    metadata = PaperMetadata()
    assert metadata.title == "No Title Found"
    assert metadata.abstract == "No Abstract Found"
    assert metadata.authors == []
    assert metadata.keywords == []


def test_evidence_item_fields():
    evidence = EvidenceItem(
        quote="The model achieved 95% accuracy.",
        page_reference="Page 4",
        relevance="Supports the reported model performance.",
    )
    assert evidence.quote == "The model achieved 95% accuracy."
    assert evidence.page_reference == "Page 4"
    assert evidence.relevance


def test_extraction_accepts_valid_data():
    extraction = Extraction(
        paper_id="paper-001",
        criterion_id="empirical_rigor",
        score=4,
        score_justification="The methodology is clearly described.",
        evidence=[
            EvidenceItem(
                quote="The sample included 100 participants.",
                page_reference="Page 3",
                relevance="Provides sample-size evidence.",
            )
        ],
        strengths=["Clear methodology"],
        weaknesses=["Limited external validation"],
        confidence=0.8,
        model_used="test-model",
    )

    assert extraction.score == 4
    assert extraction.confidence == 0.8
    assert len(extraction.evidence) == 1
