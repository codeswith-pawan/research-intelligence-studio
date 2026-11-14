import json
import subprocess
from pathlib import Path
from fastapi.testclient import TestClient
import web_app


client = TestClient(web_app.app)


def test_evidence_for_review_api():
    """Verify GET /api/evidence-for-review returns evidence for historical runs."""
    run_dir = "run_upload_20261028_005048_2b50a89b"
    review_fn = "1_reserch_ollama_llama3.2:3b_ollama_llama3.2:3b_20261028_005811.md"

    response = client.get(f"/api/evidence-for-review/{run_dir}/{review_fn}")
    assert response.status_code == 200
    data = response.json()
    assert "evidence" in data
    assert len(data["evidence"]) == 8

    # Verify counts
    verified = [e for e in data["evidence"] if e.get("verification_status") == "Verified"]
    unverified = [e for e in data["evidence"] if e.get("verification_status") == "Unverified"]
    assert len(verified) == 2
    assert len(unverified) == 6


def test_evidence_api_fallback_to_prefix():
    """Verify GET /api/evidence fallback finds evidence even when review stem or paper stem is supplied."""
    run_dir = "run_upload_20261028_005048_2b50a89b"
    # Call with paper stem
    res1 = client.get(f"/api/evidence/{run_dir}/1_reserch.pdf")
    assert res1.status_code == 200
    assert len(res1.json()["evidence"]) == 8

    # Call with review filename directly
    res2 = client.get(f"/api/evidence/{run_dir}/1_reserch_ollama_llama3.2:3b_ollama_llama3.2:3b_20261028_005811.md")
    assert res2.status_code == 200
    assert len(res2.json()["evidence"]) == 8


def test_frontend_evidence_count_consistency():
    """Verify that renderPublicationReport in app.js extracts the canonical evidence counts without 0-quote bugs."""
    js_script = """
    const fs = require('fs');
    const appJs = fs.readFileSync('web/static/app.js', 'utf8');

    // Extract helper functions
    const helpers = `
        function escapeHtml(s) { return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
        function getStatusBadgeClass(rec) { return 'status-manual-review'; }
        function formatEvidenceCitations(s) { return s; }
    `;

    // Extract renderPublicationReport function
    const fnMatch = appJs.match(/function renderPublicationReport[\\s\\S]*?\\n\\}/);
    if (!fnMatch) {
        console.error("renderPublicationReport not found");
        process.exit(1);
    }

    eval(helpers + '\\n' + fnMatch[0]);

    // Scenario A: 2 verified, 6 unverified (Real historical run)
    const md2_6 = `# Review: GI Endoscopy AI
**Review Status:** **MANUAL_REVIEW**
**Criteria:** 8/8 successful; 0 failed
**Verified Evidence:** 2
**Unverified Evidence:** 6
**Final System Recommendation:** **NEEDS MANUAL REVIEW**
**Provisional Recommendation:** Revise and Resubmit
**Overall Score:** **65.6 / 100**
`;
    const html2_6 = renderPublicationReport(md2_6);
    if (!html2_6.includes("Verified Evidence: 2 / 8 (25%)")) {
        console.error("Failed Scenario A: expected 'Verified Evidence: 2 / 8 (25%)', got:", html2_6);
        process.exit(1);
    }
    if (html2_6.includes("Verified Quotes: 0")) {
        console.error("Failed Scenario A: contains conflicting 'Verified Quotes: 0'");
        process.exit(1);
    }

    // Scenario B: 0 verified, 8 unverified
    const md0_8 = `# Review: Unverified Paper
**Review Status:** **MANUAL_REVIEW**
**Criteria:** 8/8 successful; 0 failed
**Verified Evidence:** 0
**Unverified Evidence:** 8
**Final System Recommendation:** **NEEDS MANUAL REVIEW**
`;
    const html0_8 = renderPublicationReport(md0_8);
    if (!html0_8.includes("Verified Evidence: 0 / 8 (0%)")) {
        console.error("Failed Scenario B: expected 'Verified Evidence: 0 / 8 (0%)'");
        process.exit(1);
    }

    // Scenario C: 8 verified, 0 unverified (100% verified)
    const md8_0 = `# Review: Perfect Paper
**Review Status:** **COMPLETE**
**Criteria:** 8/8 successful; 0 failed
**Verified Evidence:** 8
**Unverified Evidence:** 0
**Final System Recommendation:** **ACCEPT**
`;
    const html8_0 = renderPublicationReport(md8_0);
    if (!html8_0.includes("Verified Evidence: 8 / 8 (100%)")) {
        console.error("Failed Scenario C: expected 'Verified Evidence: 8 / 8 (100%)'");
        process.exit(1);
    }

    console.log("All evidence count consistency tests passed!");
    """;

    res = subprocess.run(["node", "-e", js_script], capture_output=True, text=True)
    assert res.returncode == 0, f"JS Test Failed: {res.stderr}\n{res.stdout}"


def test_modal_subnav_tab_switching_logic():
    """Verify that switchModalSection in app.js cleanly controls visibility for all 5 sections."""
    js_script = """
    const fs = require('fs');
    const appJs = fs.readFileSync('web/static/app.js', 'utf8');

    // Mock DOM elements
    const elements = {};
    function makeClassList(initial = []) {
        const s = new Set(initial);
        return {
            add: (c) => s.add(c),
            remove: (c) => s.delete(c),
            contains: (c) => s.has(c),
            has: (c) => s.has(c)
        };
    }

    const pills = [
        { dataset: { target: 'review-modal-body' }, classList: makeClassList(['active']), setAttribute: () => {}, removeAttribute: () => {} },
        { dataset: { target: 'evidence-panel' }, classList: makeClassList(), setAttribute: () => {}, removeAttribute: () => {} },
        { dataset: { target: 'section-analysis-panel' }, classList: makeClassList(), setAttribute: () => {}, removeAttribute: () => {} },
        { dataset: { target: 'quality-analysis-panel' }, classList: makeClassList(), setAttribute: () => {}, removeAttribute: () => {} },
        { dataset: { target: 'audit-provenance-panel' }, classList: makeClassList(), setAttribute: () => {}, removeAttribute: () => {} }
    ];

    const panels = {
        'review-modal-body': { style: { display: 'block' }, classList: makeClassList(['active']), offsetWidth: 100 },
        'evidence-panel': { style: { display: 'none' }, classList: makeClassList(), offsetWidth: 100 },
        'section-analysis-panel': { style: { display: 'none' }, classList: makeClassList(), offsetWidth: 100 },
        'quality-analysis-panel': { style: { display: 'none' }, classList: makeClassList(), offsetWidth: 100 },
        'audit-provenance-panel': { style: { display: 'none' }, classList: makeClassList(), offsetWidth: 100 }
    };

    for (const k of Object.keys(panels)) {
        panels[k].removeAttribute = () => {};
        panels[k].setAttribute = () => {};
    }

    global.document = {
        querySelectorAll: (sel) => sel === '.modal-nav-pill' ? pills : [],
        getElementById: (id) => panels[id] || null
    };

    const fnMatch = appJs.match(/function switchModalSection[\\s\\S]*?\\n\\}/);
    if (!fnMatch) {
        console.error("switchModalSection not found");
        process.exit(1);
    }
    eval(fnMatch[0]);

    // Test 1: Switch to Grounding Matrix ('evidence-panel')
    switchModalSection('evidence-panel');
    if (panels['evidence-panel'].style.display !== 'block' || !panels['evidence-panel'].classList.has('active')) {
        console.error("evidence-panel was not activated");
        process.exit(1);
    }
    if (panels['review-modal-body'].style.display !== 'none' || panels['review-modal-body'].classList.has('active')) {
        console.error("review-modal-body was not deactivated");
        process.exit(1);
    }

    // Test 2: Switch to Section Analysis
    switchModalSection('section-analysis-panel');
    if (panels['section-analysis-panel'].style.display !== 'block') {
        console.error("section-analysis-panel was not activated");
        process.exit(1);
    }
    if (panels['evidence-panel'].style.display !== 'none') {
        console.error("evidence-panel was not deactivated when switching away");
        process.exit(1);
    }

    // Test 3: Switch to Supplemental Quality
    switchModalSection('quality-analysis-panel');
    if (panels['quality-analysis-panel'].style.display !== 'block') {
        console.error("quality-analysis-panel was not activated");
        process.exit(1);
    }

    // Test 4: Switch to Audit Provenance
    switchModalSection('audit-provenance-panel');
    if (panels['audit-provenance-panel'].style.display !== 'block') {
        console.error("audit-provenance-panel was not activated");
        process.exit(1);
    }

    // Test 5: Switch back to Report
    switchModalSection('review-modal-body');
    if (panels['review-modal-body'].style.display !== 'block') {
        console.error("review-modal-body was not restored");
        process.exit(1);
    }

    console.log("All modal subnav tab switching tests passed!");
    """;

    res = subprocess.run(["node", "-e", js_script], capture_output=True, text=True)
    assert res.returncode == 0, f"JS Test Failed: {res.stderr}\n{res.stdout}"


def test_export_audit_json_accuracy():
    """Verify GET /api/export/audit returns machine-readable JSON matching canonical counts."""
    run_dir = "run_upload_20261028_005048_2b50a89b"
    review_fn = "1_reserch_ollama_llama3.2:3b_ollama_llama3.2:3b_20261028_005811.md"

    response = client.get(f"/api/export/audit/{run_dir}/{review_fn}")
    assert response.status_code == 200
    data = response.json()

    assert data["export_type"] == "full_research_audit"
    assert data["run_directory"] == run_dir
    assert data["governance_invariants"]["diagnostic_score"] == 65.6
    assert data["governance_invariants"]["provisional_recommendation"] == "Revise and Resubmit"
    assert data["governance_invariants"]["final_system_status"].lower() == "needs manual review"

    ev_info = data["evidence_grounding"]
    assert ev_info["total_records"] == 8
    assert ev_info["verified_count"] == 2
    assert ev_info["unverified_count"] == 6


def test_paper_overview_api_contract():
    """Verify Paper Overview endpoint returns valid contract for active runs."""
    run_dir = "run_upload_20261028_005048_2b50a89b"
    response = client.get(f"/api/overview/{run_dir}")
    assert response.status_code == 200
    data = response.json()
    assert data["run_dir"] == run_dir
    assert data["status"] in ("not_generated", "cached", "running", "queued")
