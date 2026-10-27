import json
import subprocess
from pathlib import Path
from fastapi.testclient import TestClient
import web_app


def test_audit_provenance_parsing_in_app_js():
    """Verify that loadAuditProvenanceForReview parses actual markdown counts, models, and provisional rec."""
    js_test = """
    const fs = require('fs');
    const elements = {};
    global.document = {
        getElementById: (id) => elements[id] || (elements[id] = { textContent: '', innerHTML: '' })
    };
    global.escapeHtml = (s) => s;

    const appJs = fs.readFileSync('web/static/app.js', 'utf8');
    const fnMatch = appJs.match(/function loadAuditProvenanceForReview[\\s\\S]*?\\n\\}/);
    if (!fnMatch) {
        console.error("Function loadAuditProvenanceForReview not found");
        process.exit(1);
    }
    eval(fnMatch[0]);

    // Test Case 1: Latest run format (7 verified, 1 unverified, Revise and Resubmit)
    const report1 = `# Review: Attention Is All You Need
**Review Status:** **MANUAL_REVIEW**
**Criteria:** 8/8 successful; 0 failed
**Verified Evidence:** 7
**Unverified Evidence:** 1
**Final System Recommendation:** **NEEDS MANUAL REVIEW**
**Provisional Recommendation:** Revise and Resubmit
**Review Date:** 2026-10-27 06:53

## Metadata
- **Extractor Model:** ollama/llama3.2:3b
- **Synthesizer Model:** ollama/llama3.2:3b
- **Total API Cost:** $0.0000
`;

    loadAuditProvenanceForReview('attend_all_of_yu_review.md', report1);
    const r1 = {
        evidence: elements['audit-evidence-val'].textContent,
        status: elements['audit-status-val'].innerHTML,
        recMeta: elements['audit-rec-meta'].textContent,
        models: elements['audit-models-val'].textContent
    };

    // Test Case 2: Earlier run format (7 verified, 1 unverified, Provisional Accept intercepted by gate)
    const report2 = `# Review: Attention Is All You Need
**Review Status:** **MANUAL_REVIEW**
**Criteria:** 8/8 successful; 0 failed
**Verified Evidence:** 7
**Unverified Evidence:** 1
**Final System Recommendation:** **NEEDS MANUAL REVIEW**
**Provisional Recommendation:** Accept
**Review Date:** 2026-10-26 15:35

## Metadata
- **Extractor Model:** ollama/llama3.2:3b
- **Synthesizer Model:** ollama/llama3.2:3b
- **Total API Cost:** $0.0000
`;

    loadAuditProvenanceForReview('attention_neurips.md', report2);
    const r2 = {
        evidence: elements['audit-evidence-val'].textContent,
        status: elements['audit-status-val'].innerHTML,
        recMeta: elements['audit-rec-meta'].textContent,
        models: elements['audit-models-val'].textContent
    };

    console.log(JSON.stringify({ r1, r2 }));
    """

    proc = subprocess.run(["node", "-e", js_test], capture_output=True, text=True, check=True)
    out = json.loads(proc.stdout)
    r1 = out["r1"]
    r2 = out["r2"]

    # Assertions for latest run
    assert r1["evidence"] == "7 Verified · 1 Unverified"
    assert "Needs Manual Review" in r1["status"]
    assert "Provisional: Revise and Resubmit · Intercepted by Safety Gate" in r1["recMeta"]
    assert "Extractor: ollama/llama3.2:3b · Synthesizer: ollama/llama3.2:3b" in r1["models"]

    # Assertions for earlier run (Provisional Accept with Manual Review gate)
    assert r2["evidence"] == "7 Verified · 1 Unverified"
    assert "Needs Manual Review" in r2["status"]
    assert "Provisional: Accept · Intercepted by Safety Gate" in r2["recMeta"]
    assert "Extractor: ollama/llama3.2:3b · Synthesizer: ollama/llama3.2:3b" in r2["models"]
    assert "9 Verified" not in r1["evidence"]
    assert "9 Verified" not in r2["evidence"]


def test_evidence_count_consistency_across_backend_endpoints(tmp_path, monkeypatch):
    """Ensure evidence counts match across /api/all-reviews, /api/evidence, /api/export/audit, and markdown."""
    run_dir = tmp_path / "run_test_counts"
    reviews_dir = run_dir / "outputs" / "reviews"
    evidence_dir = run_dir / "outputs" / "evidence"
    reviews_dir.mkdir(parents=True)
    evidence_dir.mkdir(parents=True)

    filename = "paper1_ollama_llama3.2:3b_ollama_llama3.2:3b_20261027_120000.md"
    report_content = "\n".join([
        "# Review: Test Manuscript",
        "**Review Status:** **MANUAL_REVIEW**",
        "**Criteria:** 8/8 successful; 0 failed",
        "**Verified Evidence:** 7",
        "**Unverified Evidence:** 1",
        "**Final System Recommendation:** **NEEDS MANUAL REVIEW**",
        "**Provisional Recommendation:** Revise and Resubmit",
        "**Overall Score:** **62.6 / 100**",
        "## Metadata",
        "- **Extractor Model:** ollama/llama3.2:3b",
        "- **Synthesizer Model:** ollama/llama3.2:3b",
    ])
    (reviews_dir / filename).write_text(report_content, encoding="utf-8")

    evidence_records = [
        {"criterion_id": f"c{i}", "quote": f"quote {i}", "verification_status": "Verified"}
        for i in range(1, 8)
    ]
    evidence_records.append(
        {"criterion_id": "c8", "quote": "unmatched quote", "verification_status": "Unverified"}
    )
    (evidence_dir / "paper1_evidence.json").write_text(json.dumps(evidence_records), encoding="utf-8")

    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(tmp_path))

    with TestClient(web_app.app) as client:
        # 1. /api/all-reviews
        resp = client.get("/api/all-reviews/run_test_counts")
        assert resp.status_code == 200
        reviews = resp.json()["reviews"]
        assert len(reviews) == 1
        meta = reviews[0]
        assert meta["verified_evidence_count"] == 7
        assert meta["unverified_evidence_count"] == 1
        assert meta["total_criteria"] == 8
        assert meta["verified_evidence_count"] + meta["unverified_evidence_count"] == 8

        # 2. /api/evidence
        ev_resp = client.get("/api/evidence/run_test_counts/paper1.pdf")
        assert ev_resp.status_code == 200
        ev_data = ev_resp.json()["evidence"]
        assert len(ev_data) == 8
        verified = sum(1 for e in ev_data if e["verification_status"].lower() == "verified")
        unverified = len(ev_data) - verified
        assert verified == 7
        assert unverified == 1

        # 3. /api/export/audit
        audit_resp = client.get(f"/api/export/audit/run_test_counts/{filename}")
        assert audit_resp.status_code == 200
        audit_json = audit_resp.json()
        grounding = audit_json["evidence_grounding"]
        assert grounding["verified_count"] == 7
        assert grounding["unverified_count"] == 1
        assert grounding["total_records"] == 8


def test_provisional_accept_with_safety_gate_separation(tmp_path, monkeypatch):
    """Ensure provisional Accept with manual-review status is never presented as final Accept."""
    run_dir = tmp_path / "run_provisional_accept"
    reviews_dir = run_dir / "outputs" / "reviews"
    reviews_dir.mkdir(parents=True)

    filename = "paper_accept_ollama_llama3.2:3b_ollama_llama3.2:3b_20261026_210000.md"
    report_content = "\n".join([
        "# Review: Attention Is All You Need",
        "**Review Status:** **MANUAL_REVIEW**",
        "**Criteria:** 8/8 successful; 0 failed",
        "**Verified Evidence:** 7",
        "**Unverified Evidence:** 1",
        "**Final System Recommendation:** **NEEDS MANUAL REVIEW**",
        "**Provisional Recommendation:** Accept",
        "**Overall Score:** **89.0 / 100**",
        "**Rationale:** The diagnostic weighted score is 89.0/100, which maps provisionally to Accept.",
    ])
    (reviews_dir / filename).write_text(report_content, encoding="utf-8")

    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(tmp_path))

    with TestClient(web_app.app) as client:
        resp = client.get("/api/all-reviews/run_provisional_accept")
        assert resp.status_code == 200
        meta = resp.json()["reviews"][0]

        # Provisional recommendation is Accept
        assert meta["provisional_recommendation"] == "Accept"
        # Final recommendation is NEVER Accept
        assert meta["recommendation"] == "NEEDS MANUAL REVIEW"
        assert meta["review_status"] == "manual_review"

        # Check export audit invariants
        audit_resp = client.get(f"/api/export/audit/run_provisional_accept/{filename}")
        assert audit_resp.status_code == 200
        invariants = audit_resp.json()["governance_invariants"]
        assert invariants["provisional_recommendation"] == "Accept"
        assert invariants["final_system_status"] == "NEEDS MANUAL REVIEW"
        assert invariants["review_status"] == "manual_review"


def test_run_isolation_and_no_cross_contamination(tmp_path, monkeypatch):
    """Ensure switching runs returns only that run's data with zero cross-run contamination."""
    run_a = tmp_path / "run_alpha"
    run_b = tmp_path / "run_beta"
    (run_a / "outputs" / "reviews").mkdir(parents=True)
    (run_b / "outputs" / "reviews").mkdir(parents=True)

    report_a = "\n".join([
        "# Review: Alpha Paper",
        "**Review Status:** **MANUAL_REVIEW**",
        "**Criteria:** 8/8 successful; 0 failed",
        "**Verified Evidence:** 7",
        "**Unverified Evidence:** 1",
        "**Final System Recommendation:** **NEEDS MANUAL REVIEW**",
        "**Provisional Recommendation:** Accept",
        "**Overall Score:** **89.0 / 100**",
    ])
    report_b = "\n".join([
        "# Review: Beta Paper",
        "**Review Status:** **FAILED**",
        "**Criteria:** 0/8 successful; 8 failed",
        "**Verified Evidence:** 0",
        "**Unverified Evidence:** 0",
        "**Final System Recommendation:** **NEEDS MANUAL REVIEW**",
        "**Provisional Recommendation:** Reject",
        "**Overall Score:** **0.0 / 100**",
    ])

    (run_a / "outputs" / "reviews" / "alpha.md").write_text(report_a, encoding="utf-8")
    (run_b / "outputs" / "reviews" / "beta.md").write_text(report_b, encoding="utf-8")

    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(tmp_path))

    with TestClient(web_app.app) as client:
        resp_a = client.get("/api/all-reviews/run_alpha")
        assert resp_a.status_code == 200
        reviews_a = resp_a.json()["reviews"]
        assert len(reviews_a) == 1
        assert reviews_a[0]["paper"] == "alpha"
        assert reviews_a[0]["provisional_recommendation"] == "Accept"
        assert reviews_a[0]["verified_evidence_count"] == 7

        resp_b = client.get("/api/all-reviews/run_beta")
        assert resp_b.status_code == 200
        reviews_b = resp_b.json()["reviews"]
        assert len(reviews_b) == 1
        assert reviews_b[0]["paper"] == "beta"
        assert reviews_b[0]["provisional_recommendation"] == "Reject"
        assert reviews_b[0]["verified_evidence_count"] == 0
