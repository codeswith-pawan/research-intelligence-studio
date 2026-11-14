/**
 * Agentic Paper Review System - Dashboard Frontend
 */

// State
let currentRunDir = "";
let currentRunId = null;
let eventSource = null;
let runStartTime = null;
let elapsedInterval = null;
let sortColumn = "display_date";
let sortAsc = false;  // default: newest first
let promptFiles = [];        // [{filename, content}, ...]
let currentPromptFilename = "";
let configDirty = {};        // {key: value} pending saves
let originalConfig = {};     // snapshot before edits
let batchActive = false;
let batchDirs = [];
let batchRunId = null;       // SSE run_id for current batch directory
let allRunsList = [];        // Rich run metadata list from /api/runs
let activeLoadToken = 0;     // Stale-data protection token
let pendingUploadFile = null;// File object cached for force-new retry
let duplicateRunData = null; // Duplicate run detection info

// DOM refs
const uploadForm = document.getElementById("upload-form");
const paperFileInput = document.getElementById("paper-file");
const uploadBtn = document.getElementById("upload-btn");
const uploadStatus = document.getElementById("upload-status");
const runSelector = document.getElementById("run-selector");
const modeSelector = document.getElementById("mode-selector");
const startBtn = document.getElementById("start-btn");
const stopBtn = document.getElementById("stop-btn");
const progressBar = document.getElementById("progress-bar");
const progressText = document.getElementById("progress-text");
const progressEta = document.getElementById("progress-eta");
const costDisplay = document.getElementById("cost-display");
const elapsedDisplay = document.getElementById("elapsed-display");
const currentPaperDisplay = document.getElementById("current-paper-display");
const resultsTbody = document.getElementById("results-tbody");
const noResults = document.getElementById("no-results");
const logContent = document.getElementById("log-content");
const configContent = document.getElementById("config-content");
const reviewModal = document.getElementById("review-modal");
const reviewModalTitle = document.getElementById("review-modal-title");
const reviewModalBody = document.getElementById("review-modal-body");
const reviewModalClose = document.getElementById("review-modal-close");
const reviewDashboard = document.getElementById("review-dashboard");
const dashboardReviewSelect = document.getElementById("dashboard-review-select");
const promptSelector = document.getElementById("prompt-selector");
const promptsEditor = document.getElementById("prompts-editor");
const promptVarsHint = document.getElementById("prompt-vars-hint");
const promptSaveBtn = document.getElementById("prompt-save-btn");
const promptReloadBtn = document.getElementById("prompt-reload-btn");
const promptValidation = document.getElementById("prompt-validation");
const criteriaEditorWrap = document.getElementById("criteria-editor-wrap");
const criteriaSaveBtn = document.getElementById("criteria-save-btn");
const criteriaReloadBtn = document.getElementById("criteria-reload-btn");
const criteriaValidation = document.getElementById("criteria-validation");
const sourcesEditor = document.getElementById("sources-editor");
const sourcesSaveBtn = document.getElementById("sources-save-btn");
const sourcesReloadBtn = document.getElementById("sources-reload-btn");
const sourcesValidation = document.getElementById("sources-validation");
const configSaveBtn = document.getElementById("config-save-btn");
const costsEditor = document.getElementById("costs-editor");
const costsSaveBtn = document.getElementById("costs-save-btn");
const costsReloadBtn = document.getElementById("costs-reload-btn");
const costsLookupBtn = document.getElementById("costs-lookup-btn");
const costsValidation = document.getElementById("costs-validation");
const costsLookupResult = document.getElementById("costs-lookup-result");
const judgeBtn = document.getElementById("judge-btn");
const viewVerdictsBtn = document.getElementById("view-verdicts-btn");
const batchBtn = document.getElementById("batch-btn");
const batchStopBtn = document.getElementById("batch-stop-btn");
const batchPanel = document.getElementById("batch-panel");
const batchProgressBar = document.getElementById("batch-progress-bar");
const batchText = document.getElementById("batch-text");
const batchCurrent = document.getElementById("batch-current");
const batchDirsList = document.getElementById("batch-dirs-list");
const judgeModal = document.getElementById("judge-modal");
const judgeModalBody = document.getElementById("judge-modal-body");
const judgeModalClose = document.getElementById("judge-modal-close");

// ---- Phase 8 Helpers: Identity, Routing, and Stale-Data Protection ----

function getStatusBadgeClass(status) {
    if (!status) return "badge-ready";
    const s = String(status).toLowerCase();
    if (s.includes("manual review")) return "badge-warning";
    if (s.includes("accept")) return "badge-success";
    if (s.includes("revise")) return "badge-amber";
    if (s.includes("reject")) return "badge-danger";
    if (s.includes("in_progress")) return "badge-info";
    return "badge-ready";
}

function updateSelectedFileChip(file) {
    const chip = document.getElementById("file-confirmation-chip");
    const nameEl = document.getElementById("file-chip-name");
    const sizeEl = document.getElementById("file-chip-size");
    const dropzoneHint = document.getElementById("selected-file-name");
    if (!chip) return;
    if (file) {
        chip.style.display = "inline-flex";
        if (nameEl) nameEl.textContent = file.name;
        if (sizeEl) {
            const kb = (file.size / 1024).toFixed(1);
            const mb = (file.size / (1024 * 1024)).toFixed(2);
            sizeEl.textContent = file.size > 1024 * 1024 ? `${mb} MB` : `${kb} KB`;
        }
        if (dropzoneHint) dropzoneHint.textContent = file.name;
    } else {
        chip.style.display = "none";
        if (dropzoneHint) dropzoneHint.textContent = "No file selected";
    }
}

function resetDashboardStateToLoading() {
    const scoreEl = document.getElementById("dashboard-score");
    const donutEl = document.getElementById("dashboard-donut-score");
    const scoreFill = document.getElementById("dashboard-score-fill");
    const recEl = document.getElementById("dashboard-recommendation");
    const provEl = document.getElementById("dashboard-provisional");
    const statEl = document.getElementById("dashboard-status");
    const statDetEl = document.getElementById("dashboard-status-detail");
    const evidCountEl = document.getElementById("dashboard-evidence-count");
    const evidCapEl = document.getElementById("dashboard-evidence-caption");
    const criteriaBody = document.getElementById("dashboard-criteria-body");
    const stateBanner = document.getElementById("dashboard-state-banner");
    const stateTitle = document.getElementById("dashboard-state-title");
    const stateDesc = document.getElementById("dashboard-state-description");

    if (scoreEl) scoreEl.textContent = "—";
    if (donutEl) donutEl.textContent = "—";
    if (scoreFill) scoreFill.style.width = "0%";
    if (recEl) recEl.textContent = "Loading...";
    if (provEl) provEl.textContent = "";
    if (statEl) statEl.textContent = "Loading...";
    if (statDetEl) statDetEl.textContent = "";
    if (evidCountEl) evidCountEl.textContent = "—";
    if (evidCapEl) evidCapEl.textContent = "";
    if (criteriaBody) {
        criteriaBody.innerHTML = `
            <tr>
                <td colspan="6" style="text-align:center; padding: 24px; color: var(--text-secondary);">
                    Loading criterion evaluations for ${escapeHtml(currentRunDir)}...
                </td>
            </tr>
        `;
    }
    if (stateBanner) stateBanner.className = "dashboard-state-banner state-loading";
    if (stateTitle) stateTitle.textContent = "Loading Review Data...";
    if (stateDesc) stateDesc.textContent = "Retrieving evidence grounding, criteria analysis, and report provenance.";
}

function handleRouting() {
    const rawHash = window.location.hash || "#/overview";
    let route = rawHash.split("?")[0];
    let targetRun = null;

    if (route.startsWith("#/analysis/")) {
        targetRun = route.replace("#/analysis/", "").trim();
        route = "#/analysis";
    } else if (route.startsWith("#/paper-overview/")) {
        targetRun = route.replace("#/paper-overview/", "").trim();
        route = "#/paper-overview";
    }

    const pages = {
        "#/overview": document.getElementById("page-overview"),
        "#/paper-overview": document.getElementById("page-paper-overview"),
        "#/upload": document.getElementById("page-upload"),
        "#/analysis": document.getElementById("page-analysis"),
        "#/reviews": document.getElementById("page-reviews"),
    };

    const pageKey = pages[route] ? route : "#/overview";

    Object.entries(pages).forEach(([k, el]) => {
        if (!el) return;
        if (k === pageKey) {
            el.style.display = "block";
            el.classList.add("active");
        } else {
            el.style.display = "none";
            el.classList.remove("active");
        }
    });

    // Update navigation active tabs
    document.querySelectorAll(".nav-item").forEach(link => {
        const linkHref = link.getAttribute("href");
        if (linkHref === pageKey) {
            link.classList.add("active");
            link.setAttribute("aria-current", "page");
            link.setAttribute("aria-selected", "true");
        } else {
            link.classList.remove("active");
            link.removeAttribute("aria-current");
            link.setAttribute("aria-selected", "false");
        }
    });

    // Close configuration drawer on page navigation so it does not obstruct content
    closeConfigPanel();

    if (pageKey === "#/overview") {
        renderOverviewStats();
    } else if (pageKey === "#/paper-overview") {
        if (targetRun && targetRun !== currentRunDir) {
            currentRunDir = targetRun;
            if (runSelector) runSelector.value = targetRun;
            onRunSelected();
        } else if (!currentRunDir && allRunsList.length > 0) {
            // Automatically select a completed run (or latest run) when entering Paper Overview without explicit selection
            const completedRun = allRunsList.find(r => r.has_output) || allRunsList[0];
            currentRunDir = completedRun.name;
            if (runSelector) runSelector.value = currentRunDir;
            onRunSelected();
        } else if (currentRunDir) {
            loadPaperOverview(currentRunDir);
        } else {
            showPaperOverviewUnselected();
        }
    } else if (pageKey === "#/upload") {
        fetchBatchQueue();
    } else if (pageKey === "#/reviews") {
        renderReviewsArchive();
        fetchBatchQueue();
    } else if (pageKey === "#/analysis") {
        const emptyState = document.getElementById("analysis-empty-state");
        const runContainer = document.getElementById("analysis-run-container");
        if (targetRun) {
            if (targetRun !== currentRunDir) {
                currentRunDir = targetRun;
                if (runSelector) runSelector.value = targetRun;
                onRunSelected();
            } else {
                if (emptyState) emptyState.style.display = "none";
                if (runContainer) runContainer.style.display = "block";
                loadResults();
            }
        } else if (!currentRunDir && allRunsList.length > 0) {
            // Automatically select a completed run (or latest run) when entering Analysis Studio
            const completedRun = allRunsList.find(r => r.has_output) || allRunsList[0];
            currentRunDir = completedRun.name;
            if (runSelector) runSelector.value = currentRunDir;
            if (emptyState) emptyState.style.display = "none";
            if (runContainer) runContainer.style.display = "block";
            onRunSelected();
        } else if (currentRunDir) {
            if (emptyState) emptyState.style.display = "none";
            if (runContainer) runContainer.style.display = "block";
            loadResults();
        } else {
            if (emptyState) emptyState.style.display = "flex";
            if (runContainer) runContainer.style.display = "none";
        }
    }
}

function renderOverviewStats() {
    const totalEl = document.getElementById("overview-total-papers");
    const avgEl = document.getElementById("overview-avg-score");
    const flagEl = document.getElementById("overview-flag-rate");
    const tbody = document.getElementById("overview-recent-tbody");
    if (!totalEl || !tbody) return;

    const total = allRunsList.length;
    totalEl.textContent = total;

    const scored = allRunsList.filter(r => r.overall_score != null);
    if (scored.length > 0) {
        const avg = (scored.reduce((sum, r) => sum + r.overall_score, 0) / scored.length).toFixed(1);
        avgEl.textContent = `${avg} / 100`;
    } else {
        avgEl.textContent = "—";
    }

    const flagged = allRunsList.filter(r => {
        const s = (r.final_system_status || r.status_desc || "").toLowerCase();
        return s.includes("manual review") || s.includes("revise") || s.includes("reject");
    });
    const flagPercent = total > 0 ? Math.round((flagged.length / total) * 100) : 0;
    flagEl.textContent = `${flagPercent}% (${flagged.length} flagged)`;

    // Render recent evaluations table (up to 5 items)
    const recent = allRunsList.slice(0, 5);
    if (recent.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" class="placeholder-text">No papers evaluated yet. <a href="#/upload">Upload a manuscript</a> to start.</td></tr>`;
        return;
    }

    tbody.innerHTML = recent.map(r => {
        const title = escapeHtml(r.paper_title || r.original_filename);
        const origFile = escapeHtml(r.original_filename || r.name);
        const scoreStr = (r.overall_score != null) ? `<strong>${Number(r.overall_score).toFixed(1)}</strong> / 100` : "—";
        const statusBadge = `<span class="badge ${getStatusBadgeClass(r.final_system_status || r.status)}">${escapeHtml(r.final_system_status || r.status_desc || "Ready")}</span>`;
        return `
            <tr>
                <td>
                    <div class="recent-title">${title}</div>
                    <small class="recent-file">${origFile}</small>
                </td>
                <td><span class="run-badge">${escapeHtml(r.run_label || "Run 1")}</span></td>
                <td><span class="date-text">${escapeHtml(r.upload_date_str || "Recent")}</span></td>
                <td>${scoreStr}</td>
                <td>${statusBadge}</td>
                <td>
                    <a href="#/analysis/${encodeURIComponent(r.name)}" class="btn btn-xs btn-primary recent-open-btn">
                        Analyze
                    </a>
                </td>
            </tr>
        `;
    }).join("");
}

// ---- Dedicated Paper Overview Implementation (Phase 5) ----
let overviewPollInterval = null;

function showPaperOverviewUnselected() {
    if (overviewPollInterval) {
        clearInterval(overviewPollInterval);
        overviewPollInterval = null;
    }
    const unselected = document.getElementById("paper-overview-unselected");
    const titleEl = document.getElementById("paper-overview-title");
    const metaStrip = document.getElementById("paper-overview-meta-strip");
    const pill = document.getElementById("paper-overview-status-pill");
    const pillText = document.getElementById("po-status-text");
    const genBtn = document.getElementById("po-generate-btn");
    const regenBtn = document.getElementById("po-regenerate-btn");
    const progBanner = document.getElementById("paper-overview-progress-banner");
    const errBanner = document.getElementById("paper-overview-error-banner");
    const staleBanner = document.getElementById("paper-overview-stale-banner");
    const absCard = document.getElementById("paper-overview-abstract-card");
    const secGrid = document.getElementById("paper-overview-sections-grid");
    const readyCard = document.getElementById("paper-overview-ready-card");

    if (unselected) unselected.style.display = "block";
    if (readyCard) readyCard.style.display = "none";
    if (absCard) absCard.style.display = "none";
    if (secGrid) secGrid.style.display = "none";
    if (progBanner) progBanner.style.display = "none";
    if (errBanner) errBanner.style.display = "none";
    if (staleBanner) staleBanner.style.display = "none";

    if (titleEl) titleEl.textContent = "No Manuscript Selected";
    if (metaStrip) {
        const auth = document.getElementById("po-meta-authors");
        const yr = document.getElementById("po-meta-year");
        const fn = document.getElementById("po-meta-filename");
        const tok = document.getElementById("po-meta-tokens");
        const sha = document.getElementById("po-meta-sha256");
        if (auth) auth.textContent = "Authors: —";
        if (yr) yr.textContent = "Year: —";
        if (fn) fn.textContent = "File: —";
        if (tok) tok.textContent = "Tokens: —";
        if (sha) { sha.textContent = "SHA: —"; sha.title = "Verified Manuscript SHA-256"; }
    }
    if (pill) pill.className = "paper-overview-status-pill status-pill-not-generated";
    if (pillText) pillText.textContent = "Not Selected";
    if (genBtn) { genBtn.disabled = true; genBtn.style.display = "inline-block"; }
    if (regenBtn) regenBtn.style.display = "none";
}

async function loadPaperOverview(runDir) {
    if (!runDir) {
        showPaperOverviewUnselected();
        return;
    }

    const unselected = document.getElementById("paper-overview-unselected");
    const titleEl = document.getElementById("paper-overview-title");
    const pill = document.getElementById("paper-overview-status-pill");
    const pillText = document.getElementById("po-status-text");
    const genBtn = document.getElementById("po-generate-btn");
    const regenBtn = document.getElementById("po-regenerate-btn");
    const progBanner = document.getElementById("paper-overview-progress-banner");
    const errBanner = document.getElementById("paper-overview-error-banner");
    const staleBanner = document.getElementById("paper-overview-stale-banner");
    const absCard = document.getElementById("paper-overview-abstract-card");
    const secGrid = document.getElementById("paper-overview-sections-grid");

    // Clear placeholder
    if (unselected) unselected.style.display = "none";

    const poSelect = document.getElementById("po-run-select");
    if (poSelect && runDir) {
        poSelect.value = runDir;
    }

    // Set initial title/metadata from allRunsList if available
    const INVALID_TITLES = ["keywords", "keyword", "abstract", "untitled", "no title found", "none", "title not identified", "not identified", "introduction", "references"];
    const runInfo = allRunsList.find(r => r.name === runDir);
    let safeTitle = "Title not identified";
    if (runInfo && runInfo.paper_title && !INVALID_TITLES.includes(runInfo.paper_title.toLowerCase().trim())) {
        safeTitle = runInfo.paper_title;
    } else if (runInfo && runInfo.original_filename) {
        safeTitle = runInfo.original_filename;
    }
    if (titleEl) {
        titleEl.textContent = safeTitle;
    }
    const fn = document.getElementById("po-meta-filename");
    if (fn) fn.textContent = `File: ${runInfo ? (runInfo.original_filename || runInfo.safe_filename || "manuscript.pdf") : "manuscript.pdf"}`;

    try {
        const resp = await fetch(`/api/overview/${encodeURIComponent(runDir)}`);
        if (!resp.ok) {
            const errJson = await resp.json().catch(() => ({}));
            throw new Error(errJson.detail || `Failed to fetch overview: HTTP ${resp.status}`);
        }
        const data = await resp.json();
        applyOverviewState(runDir, data);
    } catch (e) {
        console.error("loadPaperOverview error:", e);
        if (errBanner) {
            errBanner.style.display = "flex";
            const msgEl = document.getElementById("po-error-message");
            if (msgEl) msgEl.textContent = e.message;
        }
        if (pill) pill.className = "paper-overview-status-pill status-pill-failed";
        if (pillText) pillText.textContent = "Error";
    }
}

function applyOverviewState(runDir, data) {
    const unselected = document.getElementById("paper-overview-unselected");
    const pill = document.getElementById("paper-overview-status-pill");
    const pillText = document.getElementById("po-status-text");
    const genBtn = document.getElementById("po-generate-btn");
    const regenBtn = document.getElementById("po-regenerate-btn");
    const progBanner = document.getElementById("paper-overview-progress-banner");
    const errBanner = document.getElementById("paper-overview-error-banner");
    const staleBanner = document.getElementById("paper-overview-stale-banner");
    const absCard = document.getElementById("paper-overview-abstract-card");
    const secGrid = document.getElementById("paper-overview-sections-grid");
    const readyCard = document.getElementById("paper-overview-ready-card");

    if (unselected) unselected.style.display = "none";

    const status = data.status || "not_generated";

    if (status === "cached") {
        if (overviewPollInterval) { clearInterval(overviewPollInterval); overviewPollInterval = null; }
        if (pill) pill.className = "paper-overview-status-pill status-pill-cached";
        if (pillText) pillText.textContent = "Verified Overview";
        if (genBtn) genBtn.style.display = "none";
        if (regenBtn) { regenBtn.style.display = "inline-block"; regenBtn.disabled = false; }
        if (progBanner) progBanner.style.display = "none";
        if (errBanner) errBanner.style.display = "none";
        if (staleBanner) staleBanner.style.display = "none";
        if (readyCard) readyCard.style.display = "none";

        if (data.overview) {
            renderPaperOverviewData(data.overview, data.authoritative_review);
            if (absCard) absCard.style.display = "block";
            if (secGrid) secGrid.style.display = "grid";
        }
    } else if (status === "stale") {
        if (overviewPollInterval) { clearInterval(overviewPollInterval); overviewPollInterval = null; }
        if (pill) pill.className = "paper-overview-status-pill status-pill-stale";
        if (pillText) pillText.textContent = "Stale (Source Changed)";
        if (genBtn) genBtn.style.display = "none";
        if (regenBtn) { regenBtn.style.display = "inline-block"; regenBtn.disabled = false; }
        if (progBanner) progBanner.style.display = "none";
        if (errBanner) errBanner.style.display = "none";
        if (staleBanner) staleBanner.style.display = "flex";
        if (readyCard) readyCard.style.display = "none";

        if (data.overview) {
            renderPaperOverviewData(data.overview);
            if (absCard) absCard.style.display = "block";
            if (secGrid) secGrid.style.display = "grid";
        }
    } else if (status === "running" || status === "queued") {
        if (pill) pill.className = "paper-overview-status-pill status-pill-running";
        if (pillText) pillText.textContent = status === "queued" ? "Queued..." : "Generating...";
        if (genBtn) { genBtn.style.display = "inline-block"; genBtn.disabled = true; }
        if (regenBtn) { regenBtn.style.display = "none"; regenBtn.disabled = true; }
        if (errBanner) errBanner.style.display = "none";
        if (staleBanner) staleBanner.style.display = "none";
        if (readyCard) readyCard.style.display = "none";
        if (progBanner) {
            progBanner.style.display = "flex";
            const stageEl = document.getElementById("po-progress-stage");
            const barEl = document.getElementById("po-progress-bar");
            const job = data.job || data;
            if (stageEl) stageEl.textContent = job.stage || (status === "queued" ? "Queued in background..." : "Analyzing manuscript across 8 dimensions...");
            if (barEl) barEl.style.width = `${job.progress || (status === "queued" ? 10 : 25)}%`;
        }
        startOverviewPolling(runDir);
    } else if (status === "failed") {
        if (overviewPollInterval) { clearInterval(overviewPollInterval); overviewPollInterval = null; }
        if (pill) pill.className = "paper-overview-status-pill status-pill-failed";
        if (pillText) pillText.textContent = "Generation Failed";
        if (genBtn) { genBtn.style.display = "inline-block"; genBtn.disabled = false; }
        if (regenBtn) regenBtn.style.display = "none";
        if (progBanner) progBanner.style.display = "none";
        if (staleBanner) staleBanner.style.display = "none";
        if (readyCard) readyCard.style.display = "none";
        if (errBanner) {
            errBanner.style.display = "flex";
            const msgEl = document.getElementById("po-error-message");
            const job = data.job || {};
            if (msgEl) msgEl.textContent = job.error || data.message || "An error occurred during overview generation.";
        }
    } else { // not_generated
        if (overviewPollInterval) { clearInterval(overviewPollInterval); overviewPollInterval = null; }
        if (pill) pill.className = "paper-overview-status-pill status-pill-not-generated";
        if (pillText) pillText.textContent = "Not Generated";
        if (genBtn) { genBtn.style.display = "inline-block"; genBtn.disabled = false; }
        if (regenBtn) regenBtn.style.display = "none";
        if (progBanner) progBanner.style.display = "none";
        if (errBanner) errBanner.style.display = "none";
        if (staleBanner) staleBanner.style.display = "none";
        if (absCard) absCard.style.display = "none";
        if (secGrid) secGrid.style.display = "none";
        if (readyCard) {
            readyCard.style.display = "block";
            const actionBtn = document.getElementById("po-ready-generate-btn");
            if (actionBtn) {
                actionBtn.onclick = () => triggerGenerateOverview(runDir, false);
            }
        }
    }
}

function startOverviewPolling(runDir) {
    if (overviewPollInterval) clearInterval(overviewPollInterval);
    overviewPollInterval = setInterval(async () => {
        if (currentRunDir !== runDir) {
            clearInterval(overviewPollInterval);
            overviewPollInterval = null;
            return;
        }
        try {
            const resp = await fetch(`/api/overview/${encodeURIComponent(runDir)}`);
            if (!resp.ok) return;
            const data = await resp.json();
            applyOverviewState(runDir, data);
        } catch (_) {}
    }, 1500);
}

async function triggerGenerateOverview(runDir, force = false) {
    if (!runDir) return;
    const pill = document.getElementById("paper-overview-status-pill");
    const pillText = document.getElementById("po-status-text");
    const genBtn = document.getElementById("po-generate-btn");
    const regenBtn = document.getElementById("po-regenerate-btn");
    const progBanner = document.getElementById("paper-overview-progress-banner");
    const errBanner = document.getElementById("paper-overview-error-banner");
    const staleBanner = document.getElementById("paper-overview-stale-banner");

    if (pill) pill.className = "paper-overview-status-pill status-pill-running";
    if (pillText) pillText.textContent = "Generating...";
    if (genBtn) genBtn.disabled = true;
    if (regenBtn) regenBtn.disabled = true;
    if (errBanner) errBanner.style.display = "none";
    if (staleBanner) staleBanner.style.display = "none";
    if (progBanner) {
        progBanner.style.display = "flex";
        const stageEl = document.getElementById("po-progress-stage");
        const barEl = document.getElementById("po-progress-bar");
        if (stageEl) stageEl.textContent = "Queuing overview generation job...";
        if (barEl) barEl.style.width = "10%";
    }

    try {
        const resp = await fetch(`/api/overview/${encodeURIComponent(runDir)}/generate?force=${force}`, {
            method: "POST"
        });
        const data = await resp.json();
        if (resp.ok && (data.status === "cached" || data.status === "running" || data.status === "queued")) {
            applyOverviewState(runDir, data);
            return;
        }
        if (resp.status === 202) {
            applyOverviewState(runDir, data);
            return;
        }
        throw new Error(data.detail || data.message || `Failed to start generation: HTTP ${resp.status}`);
    } catch (e) {
        console.error("triggerGenerateOverview error:", e);
        if (errBanner) {
            errBanner.style.display = "flex";
            const msgEl = document.getElementById("po-error-message");
            if (msgEl) msgEl.textContent = e.message;
        }
        if (pill) pill.className = "paper-overview-status-pill status-pill-failed";
        if (pillText) pillText.textContent = "Error";
        if (genBtn) genBtn.disabled = false;
        if (regenBtn) regenBtn.disabled = false;
        if (progBanner) progBanner.style.display = "none";
    }
}

function renderPaperOverviewData(ov, authoritativeReview = null) {
    if (!ov) return;
    const INVALID_TITLES = ["keywords", "keyword", "abstract", "untitled", "no title found", "none", "title not identified", "not identified", "introduction", "references"];
    const meta = ov.metadata || {};
    const runInfo = allRunsList.find(r => r.name === currentRunDir || r.name === ov.run_dir);
    const authRev = authoritativeReview || ov.authoritative_review || runInfo || {};

    let title = "";
    if (runInfo && runInfo.paper_title && !INVALID_TITLES.includes(runInfo.paper_title.toLowerCase().trim())) {
        title = runInfo.paper_title;
    } else if (authRev.paper_title && !INVALID_TITLES.includes(authRev.paper_title.toLowerCase().trim())) {
        title = authRev.paper_title;
    } else if (meta.title && !INVALID_TITLES.includes(meta.title.toLowerCase().trim()) && !meta.title.toLowerCase().startsWith("keyword")) {
        title = meta.title;
    } else if (ov.paper_title && !INVALID_TITLES.includes(ov.paper_title.toLowerCase().trim()) && !ov.paper_title.toLowerCase().startsWith("keyword")) {
        title = ov.paper_title;
    } else {
        title = "Title not identified";
    }

    const filename = authRev.manuscript_filename || ov.paper_filename || (runInfo && runInfo.original_filename) || "manuscript.pdf";
    const shaHash = authRev.file_hash_sha256 || ov.file_hash_sha256 || (runInfo && runInfo.sha256) || "";
    const authorsList = (meta.authors && meta.authors.length > 0) ? meta.authors : (ov.authors || []);
    const yearVal = meta.year || ov.year || "—";
    const tokensVal = meta.token_count || ov.estimated_token_count;

    let abstractVal = meta.abstract || ov.abstract_raw || "";
    if (!abstractVal || abstractVal.includes("|") || abstractVal.includes("@") || abstractVal.length < 25) {
        abstractVal = "Abstract not explicitly identified in manuscript text.";
    }

    const titleEl = document.getElementById("paper-overview-title");
    if (titleEl) titleEl.textContent = title;

    const auth = document.getElementById("po-meta-authors");
    const yr = document.getElementById("po-meta-year");
    const fn = document.getElementById("po-meta-filename");
    const tok = document.getElementById("po-meta-tokens");
    const sha = document.getElementById("po-meta-sha256");

    if (auth) auth.textContent = `Authors: ${authorsList.length > 0 ? authorsList.join(", ") : "Not identified in text"}`;
    if (yr) yr.textContent = `Year: ${yearVal}`;
    if (fn) fn.textContent = `File: ${filename}`;
    if (tok) tok.textContent = `Tokens: ${tokensVal ? tokensVal.toLocaleString() : "—"}`;
    if (sha) {
        sha.textContent = `SHA: ${shaHash ? shaHash.substring(0, 10) + "..." : "—"}`;
        sha.title = `Verified Manuscript SHA-256: ${shaHash}`;
    }

    const absText = document.getElementById("po-abstract-text");
    if (absText) {
        absText.textContent = abstractVal;
    }

    // Populate Authoritative Review Diagnostics Strip
    const authStrip = document.getElementById("po-authoritative-strip");
    const authScore = document.getElementById("po-auth-score");
    const authEvidence = document.getElementById("po-auth-evidence");
    const authStatus = document.getElementById("po-auth-status");
    const authRun = document.getElementById("po-auth-run");

    if (authStrip) {
        const sc = authRev.diagnostic_score != null ? authRev.diagnostic_score : (runInfo ? runInfo.overall_score : null);
        if (sc != null) {
            authStrip.style.display = "flex";
            if (authScore) authScore.textContent = `${Number(sc).toFixed(1)} / 100`;
            const ver = authRev.verified_quotations ?? (runInfo && runInfo.verified_count) ?? 0;
            const tot = authRev.total_quotations ?? (runInfo && runInfo.total_count) ?? 0;
            if (authEvidence) authEvidence.textContent = `${ver} / ${tot} verified quotations`;
            const st = authRev.review_status || (runInfo && runInfo.final_system_status) || "Completed";
            if (authStatus) {
                authStatus.textContent = st;
                authStatus.className = `identity-badge status-badge ${getStatusBadgeClass(st)}`;
            }
            if (authRun) authRun.textContent = authRev.run_id || ov.run_dir || currentRunDir || "—";
        } else {
            authStrip.style.display = "none";
        }
    }

    const sectionKeys = [
        "research_problem",
        "research_objectives",
        "methodology",
        "main_contributions",
        "experimental_setup",
        "key_results",
        "limitations",
        "future_work"
    ];

    sectionKeys.forEach(key => {
        const sec = ov.sections ? ov.sections[key] : null;
        const bodyEl = document.getElementById(`po-body-${key}`);
        const badgeEl = document.getElementById(`po-badge-${key}`);
        const refsEl = document.getElementById(`po-refs-${key}`);

        if (!sec) {
            if (badgeEl) {
                badgeEl.className = "po-badge po-badge-not-identified";
                badgeEl.textContent = "Not Identified in Text";
            }
            if (bodyEl) bodyEl.innerHTML = `<em>Not identified in manuscript text.</em>`;
            if (refsEl) refsEl.innerHTML = `<span class="po-ref-none">No explicit page references</span>`;
            return;
        }

        // Badge
        const hasSubstantiveContent = Boolean(sec.content && sec.content.trim() && !sec.content.toLowerCase().includes("not identified") && sec.content.length > 25);
        if (badgeEl) {
            if (sec.status === "verified_content") {
                badgeEl.className = "po-badge po-badge-verified";
                badgeEl.textContent = "Extracted from Manuscript";
            } else if (sec.status === "not_identified_in_text") {
                if (hasSubstantiveContent) {
                    badgeEl.className = "po-badge po-badge-synthetic";
                    badgeEl.textContent = "AI Synthesis (Unstated in Text)";
                } else {
                    badgeEl.className = "po-badge po-badge-not-identified";
                    badgeEl.textContent = "Not Identified in Text";
                }
            } else {
                badgeEl.className = "po-badge po-badge-unsupported";
                badgeEl.textContent = sec.status || "Unknown";
            }
        }

        // Body
        if (bodyEl) {
            if (sec.status === "not_identified_in_text" && !hasSubstantiveContent) {
                bodyEl.innerHTML = `<em>${escapeHtml(sec.content || "Not identified in manuscript text.")}</em>`;
            } else {
                bodyEl.textContent = sec.content || "—";
            }
        }

        // Refs
        if (refsEl) {
            const refs = Array.isArray(sec.traceable_references) ? sec.traceable_references : [];
            if (refs.length > 0) {
                refsEl.innerHTML = refs.map(r => `<span class="po-ref-pill">${escapeHtml(r)}</span>`).join(" ");
            } else {
                refsEl.innerHTML = `<span class="po-ref-none">No explicit page references</span>`;
            }
        }
    });
}

function setupPaperOverviewListeners() {
    const genBtn = document.getElementById("po-generate-btn");
    const regenBtn = document.getElementById("po-regenerate-btn");
    const retryBtn = document.getElementById("po-retry-btn");

    if (genBtn) {
        genBtn.addEventListener("click", () => triggerGenerateOverview(currentRunDir, false));
    }
    if (regenBtn) {
        regenBtn.addEventListener("click", () => triggerGenerateOverview(currentRunDir, true));
    }
    if (retryBtn) {
        retryBtn.addEventListener("click", () => triggerGenerateOverview(currentRunDir, true));
    }
}

function renderReviewsArchive() {
    const tbody = document.getElementById("results-tbody");
    const noRes = document.getElementById("no-results");
    const searchInput = document.getElementById("reviews-search-input");
    const statusFilter = document.getElementById("reviews-status-filter");
    if (!tbody) return;

    const query = searchInput ? searchInput.value.toLowerCase().trim() : "";
    const filterStatus = statusFilter ? statusFilter.value.trim() : "";

    let filtered = allRunsList.filter(r => {
        if (query) {
            const nameMatch = (r.paper_title || "").toLowerCase().includes(query)
                || (r.original_filename || "").toLowerCase().includes(query)
                || (r.name || "").toLowerCase().includes(query);
            if (!nameMatch) return false;
        }
        if (filterStatus) {
            const curStatus = (r.final_system_status || r.status_desc || "").toLowerCase();
            if (!curStatus.includes(filterStatus.toLowerCase())) return false;
        }
        return true;
    });

    if (sortColumn) {
        filtered.sort((a, b) => {
            let va, vb;
            if (sortColumn === "paper") {
                va = (a.paper_title || a.original_filename || a.name || "").toLowerCase();
                vb = (b.paper_title || b.original_filename || b.name || "").toLowerCase();
            } else if (sortColumn === "display_date") {
                va = a.upload_timestamp || "";
                vb = b.upload_timestamp || "";
            } else if (sortColumn === "overall_score") {
                va = (a.overall_score != null) ? Number(a.overall_score) : -1;
                vb = (b.overall_score != null) ? Number(b.overall_score) : -1;
            } else if (sortColumn === "recommendation") {
                va = (a.final_system_status || a.status_desc || "").toLowerCase();
                vb = (b.final_system_status || b.status_desc || "").toLowerCase();
            } else if (sortColumn === "review_status") {
                va = (a.status_desc || a.status || "").toLowerCase();
                vb = (b.status_desc || b.status || "").toLowerCase();
            } else {
                va = (a[sortColumn] || "").toString().toLowerCase();
                vb = (b[sortColumn] || "").toString().toLowerCase();
            }
            if (va < vb) return sortAsc ? -1 : 1;
            if (va > vb) return sortAsc ? 1 : -1;
            return 0;
        });
    }

    if (filtered.length === 0) {
        tbody.innerHTML = "";
        if (noRes) {
            noRes.style.display = "block";
            noRes.textContent = query || filterStatus ? "No reviews match your filter criteria." : "No reports yet";
        }
        if (judgeBtn) judgeBtn.disabled = true;
        return;
    }

    if (noRes) noRes.style.display = "none";
    if (judgeBtn) judgeBtn.disabled = false;

    tbody.innerHTML = filtered.map(r => {
        const title = escapeHtml(r.paper_title || r.original_filename || r.name);
        const origFile = escapeHtml(r.original_filename || r.name);
        const scoreVal = r.overall_score != null ? Number(r.overall_score) : null;
        const scoreHtml = scoreVal != null
            ? `<div class="table-score-wrap"><span class="table-score-bar" style="width: ${Math.min(100, Math.max(0, scoreVal))}%;"></span><strong>${scoreVal.toFixed(1)}</strong></div>`
            : "—";
        const statusHtml = `<span class="badge ${getStatusBadgeClass(r.final_system_status || r.status)}">${escapeHtml(r.final_system_status || r.status_desc || "Ready")}</span>`;
        return `
            <tr>
                <td>
                    <div class="paper-cell">
                        <strong class="paper-cell-title">${title}</strong>
                        <span class="paper-cell-file">${origFile}</span>
                        <span class="run-badge">${escapeHtml(r.run_label || "Run 1")}</span>
                    </div>
                </td>
                <td class="date-cell">${escapeHtml(r.upload_date_str || "—")}</td>
                <td class="model-cell">ollama/llama3.2:3b</td>
                <td class="model-cell">ollama/llama3.2:3b</td>
                <td class="score-cell">${scoreHtml}</td>
                <td class="rec-cell">${statusHtml}</td>
                <td><span class="status-pill status-${r.status}">${escapeHtml(r.status_desc || r.status)}</span></td>
                <td class="cost-cell">$0.00</td>
                <td><span class="reliability-indicator">Heuristic (Local)</span></td>
                <td>
                    <div class="row-actions">
                        <a href="#/analysis/${encodeURIComponent(r.name)}" class="btn btn-xs btn-primary">
                            View
                        </a>
                        <button type="button" class="btn btn-xs btn-outline-danger btn-delete-run"
                            data-run-dir="${escapeHtml(r.name)}"
                            data-paper="${escapeHtml(title)}"
                            data-label="${escapeHtml(r.run_label || 'Run 1')}"
                            title="Move this run to Trash">
                            Delete
                        </button>
                    </div>
                </td>
            </tr>
        `;
    }).join("");

    // Attach delete run handlers with clear paper/run confirmation (Phase 2 Requirement 1, 5, 6)
    tbody.querySelectorAll(".btn-delete-run").forEach(btn => {
        btn.addEventListener("click", async (e) => {
            e.stopPropagation();
            const runDir = btn.dataset.runDir;
            const paper = btn.dataset.paper;
            const label = btn.dataset.label;
            const confirmMsg = `Move this evaluation run to Trash?\n\nPaper: "${paper}" (${label})\nRun Directory: ${runDir}\n\nThis run will be hidden from the archive but can be restored from the Trash at any time.`;
            if (!confirm(confirmMsg)) {
                return;
            }
            try {
                await api(`/api/runs/${encodeURIComponent(runDir)}/delete`, { method: "POST" });
                addLog(`Moved run to Trash: ${runDir}`, "info");
                if (currentRunDir === runDir) {
                    currentRunDir = "";
                    if (runSelector) runSelector.value = "";
                    reviewDashboard.hidden = true;
                }
                await loadRuns();
                await updateTrashBadge();
            } catch (err) {
                alert("Failed to delete run: " + err.message);
            }
        });
    });
}

async function updateTrashBadge() {
    const badge = document.getElementById("trash-count-badge");
    if (!badge) return;
    try {
        const data = await api("/api/deleted-runs");
        const count = (data.deleted_runs || []).length;
        badge.textContent = count;
    } catch (_) {
        badge.textContent = "0";
    }
}

async function showTrashModal() {
    const modal = document.getElementById("trash-modal");
    const tbody = document.getElementById("trash-tbody");
    const noTrash = document.getElementById("no-trash");
    if (!modal || !tbody) return;

    tbody.innerHTML = '<tr><td colspan="5" class="placeholder-text">Loading deleted runs...</td></tr>';
    modal.style.display = "flex";

    try {
        const data = await api("/api/deleted-runs");
        const runs = data.deleted_runs || [];
        if (runs.length === 0) {
            tbody.innerHTML = "";
            if (noTrash) noTrash.style.display = "block";
            return;
        }
        if (noTrash) noTrash.style.display = "none";
        tbody.innerHTML = runs.map(r => {
            const title = escapeHtml(r.paper_title || r.original_filename || r.name);
            const origFile = escapeHtml(r.original_filename || r.name);
            const scoreVal = r.overall_score != null ? Number(r.overall_score).toFixed(1) : "—";
            const deletedAt = r.deleted_at ? new Date(r.deleted_at).toLocaleString() : "Recently";
            return `
                <tr>
                    <td>
                        <div class="paper-cell">
                            <strong class="paper-cell-title">${title}</strong>
                            <span class="paper-cell-file">${origFile}</span>
                            <span class="run-badge">${escapeHtml(r.run_label || "Run 1")}</span>
                        </div>
                    </td>
                    <td><code>${escapeHtml(r.name)}</code></td>
                    <td><strong>${scoreVal}</strong></td>
                    <td>${escapeHtml(deletedAt)}</td>
                    <td>
                        <button type="button" class="btn btn-xs btn-outline-success btn-restore-run" data-run-dir="${escapeHtml(r.name)}" title="Restore this run to Past Reviews">
                            Restore
                        </button>
                    </td>
                </tr>
            `;
        }).join("");

        tbody.querySelectorAll(".btn-restore-run").forEach(btn => {
            btn.addEventListener("click", async () => {
                const runDir = btn.dataset.runDir;
                try {
                    await api(`/api/runs/${encodeURIComponent(runDir)}/restore`, { method: "POST" });
                    addLog(`Restored run from Trash: ${runDir}`, "success");
                    await showTrashModal();
                    await loadRuns();
                    await updateTrashBadge();
                } catch (err) {
                    alert("Failed to restore run: " + err.message);
                }
            });
        });
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="5" class="placeholder-text error">Error loading trash: ${escapeHtml(err.message)}</td></tr>`;
    }
}

// ---- Initialization ----

async function init() {
    await loadRuns();
    runSelector.addEventListener("change", onRunSelected);

    // Deep link support via ?run=... query parameter
    try {
        const urlParams = new URLSearchParams(window.location.search);
        const requestedRun = urlParams.get("run");
        if (requestedRun && Array.from(runSelector.options).some(o => o.value === requestedRun)) {
            runSelector.value = requestedRun;
            await onRunSelected();
        }
        const modalTarget = urlParams.get("modal");
        if (modalTarget && dashboardReviewSelect.value) {
            await showReviewByFilename(dashboardReviewSelect.value);
            const targetId = modalTarget === "sections" ? "section-analysis-panel"
                : modalTarget === "quality" ? "quality-analysis-panel"
                : modalTarget === "audit" ? "audit-provenance-panel"
                : "evidence-panel";
            const panel = document.getElementById(targetId);
            if (panel) {
                panel.scrollIntoView({ behavior: "instant", block: "start" });
            }
        }
    } catch {}

    uploadForm.addEventListener("submit", uploadPaper);
    setupBatchListeners();
    setupPaperOverviewListeners();
    fetchBatchQueue();

    if (paperFileInput) {
        const dropzone = paperFileInput.closest(".file-dropzone");
        const nameDisplay = document.getElementById("selected-file-name");

        const updateSelectedFile = () => {
            if (!nameDisplay) return;
            const files = paperFileInput.files;
            const multiSummary = document.getElementById("multi-file-summary");
            const multiList = document.getElementById("multi-file-list");
            const multiCount = document.getElementById("multi-file-count");
            const singleChip = document.getElementById("file-confirmation-chip");

            if (!files || files.length === 0) {
                nameDisplay.textContent = "No file selected";
                nameDisplay.title = "";
                updateSelectedFileChip(null);
                if (multiSummary) multiSummary.style.display = "none";
                if (uploadBtn) uploadBtn.innerHTML = '<span aria-hidden="true">↑</span> Upload Paper';
            } else if (files.length === 1) {
                const file = files[0];
                nameDisplay.textContent = file.name;
                nameDisplay.title = file.name;
                updateSelectedFileChip(file);
                if (multiSummary) multiSummary.style.display = "none";
                if (uploadBtn) uploadBtn.innerHTML = '<span aria-hidden="true">↑</span> Upload Paper';
            } else {
                nameDisplay.textContent = `${files.length} manuscripts selected`;
                nameDisplay.title = Array.from(files).map(f => f.name).join(", ");
                if (singleChip) singleChip.style.display = "none";
                if (multiSummary && multiList && multiCount) {
                    multiSummary.style.display = "block";
                    multiCount.textContent = `${files.length} manuscripts selected for batch review`;
                    multiList.innerHTML = Array.from(files).map(f => {
                        const kb = (f.size / 1024).toFixed(1);
                        const mb = (f.size / (1024 * 1024)).toFixed(2);
                        const sizeStr = f.size > 1024 * 1024 ? `${mb} MB` : `${kb} KB`;
                        return `<div class="multi-file-item">
                            <span class="multi-file-item-name" title="${escapeHtml(f.name)}">📄 ${escapeHtml(f.name)}</span>
                            <span class="multi-file-item-size">${sizeStr}</span>
                        </div>`;
                    }).join("");
                }
                if (uploadBtn) uploadBtn.innerHTML = `<span aria-hidden="true">↑</span> Upload &amp; Queue ${files.length} Papers`;
            }

            const dupCard = document.getElementById("duplicate-prompt-card");
            if (dupCard) dupCard.style.display = "none";
        };

        paperFileInput.addEventListener("change", updateSelectedFile);

        const multiClearBtn = document.getElementById("multi-file-clear");
        if (multiClearBtn) {
            multiClearBtn.addEventListener("click", () => {
                paperFileInput.value = "";
                pendingUploadFile = null;
                updateSelectedFile();
            });
        }

        if (dropzone) {
            ["dragenter", "dragover"].forEach(eventName => {
                dropzone.addEventListener(eventName, event => {
                    event.preventDefault();
                    event.stopPropagation();
                    dropzone.classList.add("is-dragging");
                });
            });

            ["dragleave", "dragend"].forEach(eventName => {
                dropzone.addEventListener(eventName, event => {
                    event.preventDefault();
                    dropzone.classList.remove("is-dragging");
                });
            });

            dropzone.addEventListener("drop", event => {
                event.preventDefault();
                event.stopPropagation();
                dropzone.classList.remove("is-dragging");

                const files = event.dataTransfer && event.dataTransfer.files;
                if (!files || !files.length) return;

                try {
                    const transfer = new DataTransfer();
                    for (let i = 0; i < files.length; i++) {
                        transfer.items.add(files[i]);
                    }
                    paperFileInput.files = transfer.files;
                    paperFileInput.dispatchEvent(new Event("change", { bubbles: true }));
                } catch {
                    if (uploadStatus) {
                        uploadStatus.textContent =
                            "Drag-and-drop is not supported here. Please use Choose a research paper.";
                    }
                }
            });
        }
    }

    startBtn.addEventListener("click", startRun);
    stopBtn.addEventListener("click", stopRun);
    function closeReviewModal() {
        if (reviewModal) reviewModal.style.display = "none";
        if (lastModalFocusedElement && typeof lastModalFocusedElement.focus === "function") {
            try { lastModalFocusedElement.focus(); } catch (_) {}
            lastModalFocusedElement = null;
        }
    }
    reviewModalClose.addEventListener("click", closeReviewModal);
    reviewModal.addEventListener("click", (e) => {
        if (e.target === reviewModal) closeReviewModal();
    });
    if (judgeModal) {
        judgeModal.addEventListener("click", (e) => {
            if (e.target === judgeModal) judgeModal.style.display = "none";
        });
    }

    // Analysis Studio in-page start controls
    const analysisStartBtn = document.getElementById("analysis-start-btn");
    const analysisModeSelector = document.getElementById("analysis-mode-selector");
    if (analysisStartBtn) {
        analysisStartBtn.addEventListener("click", () => {
            if (analysisModeSelector && modeSelector) {
                modeSelector.value = analysisModeSelector.value;
            }
            startRun();
        });
    }
    if (analysisModeSelector && modeSelector) {
        analysisModeSelector.addEventListener("change", () => {
            modeSelector.value = analysisModeSelector.value;
        });
        modeSelector.addEventListener("change", () => {
            analysisModeSelector.value = modeSelector.value;
        });
    }

    // Sidebar tabs
    document.querySelectorAll(".sidebar-tab").forEach(tab => {
        tab.addEventListener("click", () => switchSidebarTab(tab.dataset.tab));
    });

    // Collapsible config panel
    document.getElementById("panel-toggle").addEventListener("click", toggleConfigPanel);

    // Prompt editor
    promptSelector.addEventListener("change", onPromptSelected);
    promptSaveBtn.addEventListener("click", savePrompt);
    promptReloadBtn.addEventListener("click", reloadPrompt);

    // Criteria editor
    criteriaSaveBtn.addEventListener("click", saveCriteria);
    criteriaReloadBtn.addEventListener("click", reloadCriteria);

    // Sources editor
    sourcesSaveBtn.addEventListener("click", saveSources);
    sourcesReloadBtn.addEventListener("click", reloadSources);

    // Config save
    configSaveBtn.addEventListener("click", saveConfig);

    // Costs editor
    costsSaveBtn.addEventListener("click", saveCosts);
    costsReloadBtn.addEventListener("click", reloadCosts);
    costsLookupBtn.addEventListener("click", lookupModelCost);

    // Judge
    judgeBtn.addEventListener("click", startJudge);
    judgeModalClose.addEventListener("click", () => judgeModal.style.display = "none");
    if (viewVerdictsBtn) viewVerdictsBtn.addEventListener("click", showJudgeVerdicts);

    // Batch
    batchBtn.addEventListener("click", startBatch);
    batchStopBtn.addEventListener("click", stopBatch);

    // Load global resources
    await loadPrompts();
    await loadSources();
    await loadCosts();

    // Sort headers for Past Reviews Archive
    document.querySelectorAll("#results-table th[data-sort]").forEach(th => {
        th.addEventListener("click", () => {
            const col = th.dataset.sort;
            if (sortColumn === col) sortAsc = !sortAsc;
            else { sortColumn = col; sortAsc = true; }
            renderReviewsArchive();
        });
    });

    // Refresh reports button on Past Reviews Archive
    const refreshReportsBtn = document.getElementById("refresh-reports-btn");
    if (refreshReportsBtn) {
        refreshReportsBtn.addEventListener("click", () => {
            loadRuns();
        });
    }
    dashboardReviewSelect.addEventListener("change", () => {
        if (dashboardReviewSelect.value) loadDashboardReview(dashboardReviewSelect.value);
    });

    const inspectEvidenceBtn = document.getElementById("dashboard-view-evidence-btn");
    if (inspectEvidenceBtn) {
        inspectEvidenceBtn.addEventListener("click", async () => {
            let targetFilename = (dashboardReviewSelect && dashboardReviewSelect.value)
                || (reportsData && reportsData[0] && reportsData[0].filename)
                || activeReviewFilename;

            if (!targetFilename && currentRunDir) {
                try {
                    const resp = await fetch(`/api/reports/${encodeURIComponent(currentRunDir)}`);
                    if (resp.ok) {
                        const reports = await resp.json();
                        if (reports && reports.length > 0) {
                            targetFilename = reports[0].filename;
                        }
                    }
                } catch (_) {}
            }

            if (targetFilename) {
                await showReviewByFilename(targetFilename, "evidence-panel");
            } else {
                showToast("No review report available to inspect evidence for.", "warning");
            }
        });
    }

    // Export actions
    const btnExportPdfModal = document.getElementById("btn-export-pdf");
    if (btnExportPdfModal) btnExportPdfModal.addEventListener("click", () => exportCurrentPdf());
    const btnDashExportPdf = document.getElementById("dashboard-export-pdf-btn");
    if (btnDashExportPdf) btnDashExportPdf.addEventListener("click", () => {
        if (dashboardReviewSelect && dashboardReviewSelect.value) {
            exportCurrentPdf(dashboardReviewSelect.value);
        } else {
            alert("No review report selected to export.");
        }
    });
    const btnExportMd = document.getElementById("btn-export-markdown");
    if (btnExportMd) btnExportMd.addEventListener("click", () => exportCurrentMarkdown());
    const btnExportJson = document.getElementById("btn-export-json");
    if (btnExportJson) btnExportJson.addEventListener("click", () => exportCurrentAuditJson());
    const btnPrintReview = document.getElementById("btn-print-review");
    if (btnPrintReview) btnPrintReview.addEventListener("click", printCurrentReview);
    const btnCopyFindings = document.getElementById("btn-copy-findings");
    if (btnCopyFindings) btnCopyFindings.addEventListener("click", () => {
        const text = reviewModalBody.innerText || reviewModalBody.textContent;
        if (!text || text.trim() === "Loading...") {
            showToast("No review report content loaded", "warning");
            return;
        }
        navigator.clipboard.writeText(text).then(() => {
            showToast("Review findings copied to clipboard", "success");
        }).catch(() => {
            showToast("Unable to copy to clipboard", "error");
        });
    });
    const btnExportCsv = document.getElementById("export-csv-btn");
    if (btnExportCsv) btnExportCsv.addEventListener("click", exportConsolidatedCsv);
    const btnDashExport = document.getElementById("dashboard-export-btn");
    if (btnDashExport) btnDashExport.addEventListener("click", () => {
        if (dashboardReviewSelect && dashboardReviewSelect.value) {
            exportCurrentMarkdown(dashboardReviewSelect.value);
        } else {
            alert("No review report selected to export.");
        }
    });
    const btnConfigDiscard = document.getElementById("config-discard-btn");
    if (btnConfigDiscard) btnConfigDiscard.addEventListener("click", discardConfigEdits);

    // Duplicate manuscript choice handlers (Rule 5)
    const dupBtnOpen = document.getElementById("dup-btn-open");
    if (dupBtnOpen) {
        dupBtnOpen.addEventListener("click", async () => {
            if (!duplicateRunData) return;
            const targetDir = duplicateRunData.run_dir;
            const dupCard = document.getElementById("duplicate-prompt-card");
            if (dupCard) dupCard.style.display = "none";
            if (runSelector) runSelector.value = targetDir;
            await onRunSelected();
            window.location.hash = `#/analysis/${targetDir}`;
            uploadStatus.textContent = "Opened existing review for this manuscript (zero wait, zero inference cost).";
            uploadStatus.className = "upload-status success";
        });
    }

    const dupBtnNew = document.getElementById("dup-btn-new");
    if (dupBtnNew) {
        dupBtnNew.addEventListener("click", async () => {
            const dupCard = document.getElementById("duplicate-prompt-card");
            if (dupCard) dupCard.style.display = "none";
            await uploadPaper(null, true);
        });
    }

    const fileChipClear = document.getElementById("file-chip-clear");
    if (fileChipClear) {
        fileChipClear.addEventListener("click", () => {
            if (paperFileInput) paperFileInput.value = "";
            pendingUploadFile = null;
            updateSelectedFileChip(null);
            const dupCard = document.getElementById("duplicate-prompt-card");
            if (dupCard) dupCard.style.display = "none";
            uploadStatus.textContent = "Selection cleared.";
            uploadStatus.className = "upload-status";
        });
    }

    // Archive Search & Filter
    const searchInput = document.getElementById("reviews-search-input");
    if (searchInput) {
        searchInput.addEventListener("input", renderReviewsArchive);
    }
    const statusFilter = document.getElementById("reviews-status-filter");
    if (statusFilter) {
        statusFilter.addEventListener("change", renderReviewsArchive);
    }

    const overviewResumeBtn = document.getElementById("overview-resume-btn");
    if (overviewResumeBtn) {
        overviewResumeBtn.addEventListener("click", () => {
            if (allRunsList.length > 0 && !currentRunDir) {
                const latestRun = allRunsList[0].name;
                if (runSelector) runSelector.value = latestRun;
                onRunSelected();
                overviewResumeBtn.href = `#/analysis/${latestRun}`;
            }
        });
    }

    // Trash Recovery Modal wiring (Phase 2)
    const btnViewTrash = document.getElementById("btn-view-trash");
    if (btnViewTrash) btnViewTrash.addEventListener("click", showTrashModal);
    const trashClose = document.getElementById("trash-modal-close");
    if (trashClose) {
        trashClose.addEventListener("click", () => {
            const modal = document.getElementById("trash-modal");
            if (modal) modal.style.display = "none";
        });
    }
    await updateTrashBadge();

    // Theme toggle & Command palette initialization
    initTheme();
    initCommandPalette();
    setupPaperpalInteractions();

    const headerConfigToggle = document.getElementById("header-config-toggle");
    if (headerConfigToggle) {
        headerConfigToggle.addEventListener("click", toggleConfigPanel);
    }

    // Mobile Actions Menu Toggle (<640px)
    const mobileActionsToggle = document.getElementById("mobile-actions-toggle");
    if (mobileActionsToggle) {
        mobileActionsToggle.addEventListener("click", toggleMobileActionsMenu);
    }

    // Mobile Sidebar Backdrop Click
    const sidebarBackdrop = document.getElementById("sidebar-backdrop");
    if (sidebarBackdrop) {
        sidebarBackdrop.addEventListener("click", closeMobileConfigPanel);
    }

    // Outside click to close mobile actions menu
    document.addEventListener("click", (e) => {
        const controls = document.getElementById("header-run-controls");
        const toggleBtn = document.getElementById("mobile-actions-toggle");
        if (controls && controls.classList.contains("open")) {
            if (!controls.contains(e.target) && (!toggleBtn || !toggleBtn.contains(e.target))) {
                closeMobileActionsMenu();
            }
        }
    });

    // Escape key closes mobile drawers, popups, and open modals
    window.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
            const panel = document.getElementById("config-panel");
            if (panel && panel.classList.contains("open")) {
                closeMobileConfigPanel();
            }
            const controls = document.getElementById("header-run-controls");
            if (controls && controls.classList.contains("open")) {
                closeMobileActionsMenu();
            }
            if (reviewModal && reviewModal.style.display !== "none") {
                reviewModal.style.display = "none";
            }
            if (judgeModal && judgeModal.style.display !== "none") {
                judgeModal.style.display = "none";
            }
            const trash = document.getElementById("trash-modal");
            if (trash && trash.style.display !== "none") {
                trash.style.display = "none";
            }
            const helpModal = document.getElementById("help-modal");
            if (helpModal && helpModal.style.display !== "none") {
                helpModal.style.display = "none";
            }
            const appSidebar = document.getElementById("app-sidebar");
            if (appSidebar && appSidebar.classList.contains("open")) {
                appSidebar.classList.remove("open");
                if (sidebarBackdrop) sidebarBackdrop.classList.remove("active");
            }
        }
    });

    // Viewport resize handling
    window.addEventListener("resize", () => {
        if (window.innerWidth > 900) {
            closeMobileConfigPanel();
        }
        if (window.innerWidth > 640) {
            closeMobileActionsMenu();
        }
    });

    // Ollama Real Health Check
    initOllamaHealthCheck();

    // Hash routing & navigation
    window.addEventListener("hashchange", handleRouting);
    handleRouting();

    // Warn before leaving with unsaved changes
    window.addEventListener("beforeunload", (e) => {
        if (Object.keys(configDirty).length > 0) {
            e.preventDefault();
            e.returnValue = "";
        }
    });
}

// ---- Paperpal Academic Interaction Handlers ----
function setupPaperpalInteractions() {
    // 1. Sidebar Configuration Button toggle
    const sidebarConfigBtn = document.getElementById("sidebar-config-btn");
    if (sidebarConfigBtn) {
        sidebarConfigBtn.addEventListener("click", () => {
            toggleConfigPanel();
        });
    }

    // 2. Mobile Sidebar Toggle Button
    const sidebarToggleBtn = document.getElementById("sidebar-toggle-btn");
    const appSidebar = document.getElementById("app-sidebar");
    const backdrop = document.getElementById("sidebar-backdrop");
    if (sidebarToggleBtn && appSidebar) {
        sidebarToggleBtn.addEventListener("click", () => {
            const isOpen = appSidebar.classList.toggle("open");
            if (backdrop) {
                if (isOpen) backdrop.classList.add("active");
                else backdrop.classList.remove("active");
            }
        });
    }
    if (backdrop) {
        backdrop.addEventListener("click", () => {
            if (appSidebar) appSidebar.classList.remove("open");
            closeMobileConfigPanel();
        });
    }

    // 3. Help Modal
    const helpTrigger = document.getElementById("help-modal-trigger");
    const helpModal = document.getElementById("help-modal");
    const helpClose = document.getElementById("help-modal-close");
    if (helpTrigger && helpModal) {
        helpTrigger.addEventListener("click", () => {
            helpModal.style.display = "flex";
        });
    }
    if (helpClose && helpModal) {
        helpClose.addEventListener("click", () => {
            helpModal.style.display = "none";
        });
    }
    if (helpModal) {
        helpModal.addEventListener("click", (e) => {
            if (e.target === helpModal) helpModal.style.display = "none";
        });
    }

    // 4. Primary Dashboard Upload Dropzone Interaction
    const dashDropzone = document.getElementById("dashboard-dropzone");
    const dashFileInput = document.getElementById("dashboard-file-input");
    const dashChooseBtn = document.getElementById("dashboard-choose-btn");
    const dashFileChip = document.getElementById("dashboard-file-chip");
    const dashChipName = document.getElementById("dashboard-chip-name");
    const dashChipSize = document.getElementById("dashboard-chip-size");
    const dashChipClear = document.getElementById("dashboard-chip-clear");
    const dashChipSubmit = document.getElementById("dashboard-chip-submit");
    const dashProgress = document.getElementById("dashboard-upload-progress");
    const dashProgressFill = document.getElementById("dashboard-progress-fill");
    const dashProgressText = document.getElementById("dashboard-progress-text");
    const dashUploadMsg = document.getElementById("dashboard-upload-msg");

    if (dashChooseBtn && dashFileInput) {
        dashChooseBtn.addEventListener("click", () => {
            dashFileInput.click();
        });
    }

    function handleDashboardFiles(files) {
        if (!files || !files.length) return;
        const file = files[0];
        const allowedExts = [".pdf", ".docx"];
        const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
        if (!allowedExts.includes(ext)) {
            if (dashUploadMsg) {
                dashUploadMsg.textContent = "Unsupported format. Only PDF and DOCX manuscripts are supported.";
                dashUploadMsg.className = "dashboard-upload-msg error";
            }
            return;
        }
        if (file.size > 20 * 1024 * 1024) {
            if (dashUploadMsg) {
                dashUploadMsg.textContent = "File exceeds 20 MB size limit.";
                dashUploadMsg.className = "dashboard-upload-msg error";
            }
            return;
        }

        // Show chip
        if (dashFileChip) {
            dashFileChip.style.display = "flex";
            if (dashChipName) dashChipName.textContent = file.name;
            if (dashChipSize) {
                const mb = (file.size / (1024 * 1024)).toFixed(2);
                const kb = (file.size / 1024).toFixed(1);
                dashChipSize.textContent = file.size > 1024 * 1024 ? `${mb} MB` : `${kb} KB`;
            }
        }
        if (dashUploadMsg) {
            dashUploadMsg.textContent = "Manuscript selected. Click 'Start Review' to proceed.";
            dashUploadMsg.className = "dashboard-upload-msg info";
        }

        // Transfer files to paperFileInput and trigger change
        if (paperFileInput) {
            try {
                const transfer = new DataTransfer();
                for (let i = 0; i < files.length; i++) {
                    transfer.items.add(files[i]);
                }
                paperFileInput.files = transfer.files;
                paperFileInput.dispatchEvent(new Event("change", { bubbles: true }));
            } catch (_) {}
        }
    }

    if (dashFileInput) {
        dashFileInput.addEventListener("change", () => {
            handleDashboardFiles(dashFileInput.files);
        });
    }

    if (dashChipClear) {
        dashChipClear.addEventListener("click", () => {
            if (dashFileInput) dashFileInput.value = "";
            if (paperFileInput) paperFileInput.value = "";
            if (dashFileChip) dashFileChip.style.display = "none";
            if (dashUploadMsg) {
                dashUploadMsg.textContent = "";
                dashUploadMsg.className = "dashboard-upload-msg";
            }
        });
    }

    if (dashChipSubmit) {
        dashChipSubmit.addEventListener("click", async () => {
            if (dashProgress) {
                dashProgress.style.display = "block";
                if (dashProgressFill) dashProgressFill.style.width = "40%";
                if (dashProgressText) dashProgressText.textContent = "Uploading to review sandbox...";
            }
            await uploadPaper();
            if (dashProgressFill) dashProgressFill.style.width = "100%";
            setTimeout(() => {
                if (dashProgress) dashProgress.style.display = "none";
                window.location.hash = "#/upload";
            }, 600);
        });
    }

    if (dashDropzone) {
        ["dragenter", "dragover"].forEach(eventName => {
            dashDropzone.addEventListener(eventName, e => {
                e.preventDefault();
                e.stopPropagation();
                dashDropzone.classList.add("is-dragging");
            });
        });
        ["dragleave", "dragend"].forEach(eventName => {
            dashDropzone.addEventListener(eventName, e => {
                e.preventDefault();
                dashDropzone.classList.remove("is-dragging");
            });
        });
        dashDropzone.addEventListener("drop", e => {
            e.preventDefault();
            e.stopPropagation();
            dashDropzone.classList.remove("is-dragging");
            const files = e.dataTransfer && e.dataTransfer.files;
            if (files && files.length) {
                handleDashboardFiles(files);
            }
        });
    }
}

// ---- Premium UI/UX 2.0 Helpers: Toast, Themes, Radial Gauge, Command Palette ----

function getScrollBehavior() {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
}

function showToast(message, type = "info", duration = 3500) {
    const container = document.getElementById("toast-container");
    if (!container) return;

    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;

    let iconSvg = "";
    if (type === "success") {
        iconSvg = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="color:var(--status-success)"><polyline points="20 6 9 17 4 12"></polyline></svg>';
    } else if (type === "warning") {
        iconSvg = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="color:var(--status-warning)"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>';
    } else if (type === "error") {
        iconSvg = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="color:var(--status-error)"><circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line></svg>';
    } else {
        iconSvg = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="color:var(--brand-secondary)"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>';
    }

    toast.innerHTML = `${iconSvg}<span>${escapeHtml(message)}</span>`;
    container.appendChild(toast);

    const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    setTimeout(() => {
        if (prefersReducedMotion) {
            toast.remove();
        } else {
            toast.style.opacity = "0";
            toast.style.transform = "translateX(20px)";
            toast.style.transition = "all 0.3s var(--ease-out)";
            setTimeout(() => toast.remove(), 300);
        }
    }, duration);
}

function initTheme() {
    const urlParams = new URLSearchParams(window.location.search);
    const themeParam = urlParams.get("theme");
    const saved = themeParam || localStorage.getItem("ris_theme") || "light";
    setTheme(saved, false);

    const toggleBtn = document.getElementById("theme-toggle-btn");
    if (toggleBtn) {
        toggleBtn.addEventListener("click", () => {
            const current = document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
            const next = current === "light" ? "dark" : "light";
            setTheme(next, true);
        });
    }
}

function setTheme(theme, notify = false) {
    if (theme === "light") {
        document.documentElement.setAttribute("data-theme", "light");
    } else {
        document.documentElement.removeAttribute("data-theme");
    }
    localStorage.setItem("ris_theme", theme);

    const moon = document.getElementById("theme-icon-moon");
    const sun = document.getElementById("theme-icon-sun");
    if (moon && sun) {
        moon.style.display = theme === "light" ? "none" : "block";
        sun.style.display = theme === "light" ? "block" : "none";
    }
    if (notify) {
        showToast(`Switched to ${theme === "light" ? "Light" : "Dark"} mode`, "info", 2000);
    }
}

function updateRadialScoreGauge(score) {
    const circle = document.getElementById("dashboard-score-circle");
    if (!circle) return;
    if (score === null || isNaN(score) || score === undefined) {
        circle.style.strokeDashoffset = "263.89";
        circle.style.stroke = "var(--surface-raised)";
        return;
    }
    const circumference = 263.89; // 2 * PI * 42
    const clamped = Math.max(0, Math.min(100, score));
    const offset = circumference - (clamped / 100) * circumference;
    circle.style.strokeDashoffset = String(offset);

    if (clamped >= 75) {
        circle.style.stroke = "var(--status-success)";
    } else if (clamped >= 50) {
        circle.style.stroke = "var(--status-warning)";
    } else {
        circle.style.stroke = "var(--status-error)";
    }
}

let commandPaletteSelectedIndex = 0;
let commandPaletteItems = [];

function initCommandPalette() {
    const palette = document.getElementById("command-palette");
    const searchInput = document.getElementById("command-palette-search");
    const triggerBtn = document.getElementById("cmd-palette-btn");
    const closeBtn = document.getElementById("command-palette-close");

    if (!palette || !searchInput) return;

    function openPalette() {
        palette.style.display = "flex";
        searchInput.value = "";
        commandPaletteSelectedIndex = 0;
        renderCommandPaletteResults("");
        searchInput.focus();
    }

    function closePalette() {
        palette.style.display = "none";
    }

    if (triggerBtn) triggerBtn.addEventListener("click", openPalette);
    if (closeBtn) closeBtn.addEventListener("click", closePalette);

    if (new URLSearchParams(window.location.search).get("palette") === "open") {
        setTimeout(openPalette, 600);
    }

    palette.addEventListener("click", (e) => {
        if (e.target === palette) closePalette();
    });

    window.addEventListener("keydown", (e) => {
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
            e.preventDefault();
            if (palette.style.display === "none" || !palette.style.display) {
                openPalette();
            } else {
                closePalette();
            }
        } else if (e.key === "Escape" && palette.style.display !== "none") {
            closePalette();
        }
    });

    searchInput.addEventListener("input", () => {
        commandPaletteSelectedIndex = 0;
        renderCommandPaletteResults(searchInput.value.trim().toLowerCase());
    });

    searchInput.addEventListener("keydown", (e) => {
        if (e.key === "ArrowDown") {
            e.preventDefault();
            if (commandPaletteItems.length > 0) {
                commandPaletteSelectedIndex = (commandPaletteSelectedIndex + 1) % commandPaletteItems.length;
                highlightSelectedCommandItem();
            }
        } else if (e.key === "ArrowUp") {
            e.preventDefault();
            if (commandPaletteItems.length > 0) {
                commandPaletteSelectedIndex = (commandPaletteSelectedIndex - 1 + commandPaletteItems.length) % commandPaletteItems.length;
                highlightSelectedCommandItem();
            }
        } else if (e.key === "Enter") {
            e.preventDefault();
            if (commandPaletteItems[commandPaletteSelectedIndex]) {
                commandPaletteItems[commandPaletteSelectedIndex].action();
                closePalette();
            }
        }
    });
}

function renderCommandPaletteResults(query) {
    const resultsContainer = document.getElementById("command-palette-results");
    if (!resultsContainer) return;

    const baseActions = [
        { section: "Navigation", label: "System Overview", icon: "📊", badge: "Page", action: () => { window.location.hash = "#/overview"; } },
        { section: "Navigation", label: "Paper Overview (8 Dimensions)", icon: "📑", badge: "Page", action: () => { window.location.hash = "#/paper-overview"; } },
        { section: "Navigation", label: "Upload Research Paper", icon: "↑", badge: "Page", action: () => { window.location.hash = "#/upload"; } },
        { section: "Navigation", label: "Analysis Studio", icon: "🔬", badge: "Page", action: () => { window.location.hash = "#/analysis"; } },
        { section: "Navigation", label: "Past Reviews Archive", icon: "🗄️", badge: "Page", action: () => { window.location.hash = "#/reviews"; } },
        { section: "Actions", label: "Start Review on Active Run", icon: "▶", badge: "Action", action: () => { if (startBtn && !startBtn.disabled) startRun(); else showToast("Please select a run first", "warning"); } },
        { section: "Actions", label: "Execute Batch Review", icon: "⚡", badge: "Batch", action: () => { startBatch(); } },
        { section: "Actions", label: "Toggle Dark / Light Theme", icon: "🌓", badge: "Theme", action: () => { const curr = document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark"; setTheme(curr === "light" ? "dark" : "light", true); } },
        { section: "Actions", label: "Toggle Configuration Drawer", icon: "⚙️", badge: "Sidebar", action: () => { toggleConfigPanel(); } },
        { section: "Actions", label: "View Soft-Deleted Runs (Trash)", icon: "🗑️", badge: "Trash", action: () => { showTrashModal(); } },
        { section: "Actions", label: "Export Consolidated CSV", icon: "📥", badge: "Export", action: () => { window.location.href = "/api/reports/csv"; } },
    ];

    const paperActions = (allRunsList || []).map(r => ({
        section: "Recent Manuscripts",
        label: r.paper_title || r.original_filename || r.name,
        sub: `${r.formatted_summary || r.display_name || r.name} · Score: ${r.overall_score != null ? r.overall_score.toFixed(1) : "—"}`,
        icon: "📄",
        badge: r.run_label || "Run",
        action: () => {
            window.location.hash = `#/analysis/${encodeURIComponent(r.name)}`;
        }
    }));

    const all = [...baseActions, ...paperActions];
    commandPaletteItems = query ? all.filter(item => 
        item.label.toLowerCase().includes(query) || 
        (item.sub && item.sub.toLowerCase().includes(query)) ||
        item.section.toLowerCase().includes(query)
    ) : all;

    if (commandPaletteItems.length === 0) {
        resultsContainer.innerHTML = `<div class="placeholder-text">No matching commands or manuscripts found for "${escapeHtml(query)}"</div>`;
        return;
    }

    let html = "";
    let currentSection = "";
    commandPaletteItems.forEach((item, index) => {
        if (item.section !== currentSection) {
            currentSection = item.section;
            html += `<div class="command-section-label">${escapeHtml(currentSection)}</div>`;
        }
        const isSelected = index === commandPaletteSelectedIndex ? "selected" : "";
        html += `<div class="command-item ${isSelected}" data-index="${index}">
            <div class="command-item-left">
                <span>${item.icon}</span>
                <div>
                    <div>${escapeHtml(item.label)}</div>
                    ${item.sub ? `<small style="font-size:10px; color:var(--text-muted);">${escapeHtml(item.sub)}</small>` : ""}
                </div>
            </div>
            <span class="command-item-badge">${escapeHtml(item.badge)}</span>
        </div>`;
    });

    resultsContainer.innerHTML = html;

    resultsContainer.querySelectorAll(".command-item").forEach(el => {
        el.addEventListener("click", () => {
            const idx = Number(el.dataset.index);
            if (commandPaletteItems[idx]) {
                commandPaletteItems[idx].action();
                const palette = document.getElementById("command-palette");
                if (palette) palette.style.display = "none";
            }
        });
    });
}

function highlightSelectedCommandItem() {
    const resultsContainer = document.getElementById("command-palette-results");
    if (!resultsContainer) return;
    resultsContainer.querySelectorAll(".command-item").forEach((el, idx) => {
        if (idx === commandPaletteSelectedIndex) {
            el.classList.add("selected");
            el.scrollIntoView({ block: "nearest" });
        } else {
            el.classList.remove("selected");
        }
    });
}

// ---- API helpers ----

async function api(path, opts = {}) {
    const res = await fetch(path, opts);
    if (!res.ok) {
        let detail = `${res.status}: ${res.statusText}`;
        try {
            const body = await res.json();
            if (body.detail) detail = body.detail;
        } catch {}
        throw new Error(detail);
    }
    return res.json();
}

// ---- Paper Upload ----

async function uploadPaper(event, forceNew = false) {
    if (event) event.preventDefault();

    const files = paperFileInput.files;
    const isMulti = files && files.length > 1;

    // Check duplicate policy select
    const dupPolicySelect = document.getElementById("duplicate-policy-select");
    const dupPolicy = dupPolicySelect ? dupPolicySelect.value : "skip";
    if (dupPolicy === "force_new") {
        forceNew = true;
    }

    if (!files || files.length === 0) {
        if (!pendingUploadFile) {
            uploadStatus.textContent = "Please choose one or more PDF or DOCX files first.";
            uploadStatus.className = "upload-status error";
            return;
        }
    }

    const allowedExtensions = [".pdf", ".docx"];
    const filesToUpload = files && files.length > 0 ? Array.from(files) : [pendingUploadFile];

    for (const f of filesToUpload) {
        const ext = f.name.slice(f.name.lastIndexOf(".")).toLowerCase();
        if (!allowedExtensions.includes(ext)) {
            uploadStatus.textContent = `Unsupported file type for "${f.name}". Only PDF and DOCX manuscripts are supported.`;
            uploadStatus.className = "upload-status error";
            return;
        }
        if (f.size > 20 * 1024 * 1024) {
            uploadStatus.textContent = `File "${f.name}" exceeds the 20 MB size limit. Please upload smaller files.`;
            uploadStatus.className = "upload-status error";
            return;
        }
    }

    const formData = new FormData();
    if (isMulti) {
        for (const f of filesToUpload) {
            formData.append("files", f);
        }
        formData.append("is_batch", "true");
        formData.append("duplicate_policy", dupPolicy);
    } else {
        formData.append("file", filesToUpload[0]);
        if (forceNew) {
            formData.append("force_new", "true");
        }
    }

    // Reuse model settings from the currently selected run if available.
    if (runSelector && runSelector.value) {
        formData.append("base_run_dir", runSelector.value);
    }

    uploadBtn.disabled = true;
    uploadStatus.className = "upload-status info";
    uploadStatus.textContent = isMulti ? `Uploading ${filesToUpload.length} manuscripts into batch queue...` : `Uploading ${filesToUpload[0].name}...`;

    const dupCard = document.getElementById("duplicate-prompt-card");
    if (dupCard) dupCard.style.display = "none";

    try {
        const result = await api("/api/upload", {
            method: "POST",
            body: formData
        });

        if (result.status === "batch_staged") {
            uploadStatus.className = "upload-status success";
            uploadStatus.textContent = `Staged ${result.total_files} manuscripts (${result.queued_count} queued, ${result.skipped_count} skipped duplicates). Starting batch execution...`;
            const uploadBatchSection = document.getElementById("upload-batch-section");
            if (uploadBatchSection) uploadBatchSection.style.display = "block";

            await fetchBatchQueue();

            if (result.queued_count > 0) {
                try {
                    await api("/api/batch-start", {
                        method: "POST",
                        body: JSON.stringify({ mode: modeSelector.value })
                    });
                } catch (e) {
                    // Ignore conflict if already running
                }
                startBatchPolling();
            }
            return;
        }

        if (result.status === "duplicate" && !forceNew) {
            duplicateRunData = result;
            if (dupCard) {
                dupCard.style.display = "flex";
                const desc = document.getElementById("duplicate-desc");
                if (desc) {
                    const scoreText = result.overall_score != null ? ` · Score: ${result.overall_score}/100` : "";
                    const statusText = result.final_system_status || "Completed";
                    desc.textContent = `This exact manuscript matches a previous analysis: "${result.paper_title || result.original_filename}" (${result.run_label || "Run 1"}, uploaded ${result.upload_date_str || "previously"} · ${statusText}${scoreText}).`;
                }
            }
            uploadStatus.textContent = "Duplicate manuscript detected. Please choose to open existing review or run a clean new review.";
            uploadStatus.className = "upload-status warning";
            return;
        }

        await loadRuns();
        if (runSelector) {
            runSelector.value = result.run_dir;
        }
        await onRunSelected();

        uploadStatus.className = "upload-status success";
        uploadStatus.textContent = `Uploaded ${result.original_filename || result.filename}. Ready for analysis.`;
        window.location.hash = `#/analysis/${result.run_dir}`;
    } catch (error) {
        uploadStatus.className = "upload-status error";
        uploadStatus.textContent = `Upload failed: ${error.message}`;
    } finally {
        uploadBtn.disabled = false;
    }
}

// ---- Run Directory Selection ----

async function loadRuns() {
    try {
        const data = await api("/api/runs");
        allRunsList = data.runs || [];

        // Update nav badge count
        const navCount = document.getElementById("nav-reviews-count");
        if (navCount) navCount.textContent = allRunsList.length;

        runSelector.innerHTML = '<option value="">Select run directory...</option>';
        for (const run of allRunsList) {
            const opt = document.createElement("option");
            opt.value = run.name;
            const primary = run.formatted_summary || run.display_name || run.name;
            const secondary = run.formatted_secondary || (run.upload_date_str ? `${run.upload_date_str} · ${run.status}` : `${run.paper_count} papers`);
            opt.textContent = `${primary} (${secondary})`;
            runSelector.appendChild(opt);
        }

        const poSelect = document.getElementById("po-run-select");
        if (poSelect) {
            poSelect.innerHTML = '<option value="">Select manuscript...</option>';
            for (const run of allRunsList) {
                const opt = document.createElement("option");
                opt.value = run.name;
                const primary = run.paper_title || run.original_filename || run.name;
                opt.textContent = `${primary} (${run.run_label || 'Run 1'})`;
                poSelect.appendChild(opt);
            }
            if (currentRunDir) {
                poSelect.value = currentRunDir;
            }
            poSelect.onchange = () => {
                if (poSelect.value) {
                    if (runSelector) runSelector.value = poSelect.value;
                    onRunSelected();
                    loadPaperOverview(poSelect.value);
                }
            };
        }

        renderOverviewStats();
        renderReviewsArchive();
        await updateTrashBadge();

        // Re-check target run from hash now that options are populated
        const currentHash = window.location.hash || "";
        let pendingTarget = null;
        if (currentHash.startsWith("#/analysis/")) {
            pendingTarget = currentHash.replace("#/analysis/", "").trim();
        } else if (currentHash.startsWith("#/paper-overview/")) {
            pendingTarget = currentHash.replace("#/paper-overview/", "").trim();
        }
        if (pendingTarget && allRunsList.some(r => r.name === pendingTarget)) {
            if (currentRunDir !== pendingTarget) {
                runSelector.value = pendingTarget;
                await onRunSelected();
            }
        }
    } catch (e) {
        console.error("Failed to load runs:", e);
    }
}

async function onRunSelected() {
    if (Object.keys(configDirty).length > 0) {
        if (!confirm("You have unsaved configuration changes. Discard and switch runs?")) {
            runSelector.value = currentRunDir;
            return;
        }
        discardConfigEdits();
    }
    currentRunDir = runSelector.value;
    startBtn.disabled = !currentRunDir;

    const currentToken = ++activeLoadToken;

    // Immediately stop active SSE and timers if running
    if (eventSource) {
        try { eventSource.close(); } catch (_) {}
        eventSource = null;
    }
    if (elapsedInterval) {
        clearInterval(elapsedInterval);
        elapsedInterval = null;
    }

    // Close review modal & evidence drawer immediately to prevent cross-run state bleeding (Requirement 8.5)
    if (reviewModal) reviewModal.style.display = "none";
    const evidenceDrawer = document.getElementById("evidence-panel");
    if (evidenceDrawer) evidenceDrawer.classList.remove("active");

    // Atomically reset dashboard to loading state
    reportsData = [];
    configDirty = {};
    originalConfig = {};
    if (configSaveBtn) configSaveBtn.style.display = "none";
    logContent.innerHTML = "";
    progressBar.style.width = "0%";
    progressText.textContent = "";
    progressEta.textContent = "";
    costDisplay.textContent = "$0.00";
    elapsedDisplay.textContent = "0s";
    currentPaperDisplay.textContent = "";
    clearStages();
    if (viewVerdictsBtn) viewVerdictsBtn.style.display = "none";

    // Immediate skeleton reset on metrics and score cards (Requirement 8.5)
    resetDashboardStateToLoading();

    const emptyState = document.getElementById("analysis-empty-state");
    const runContainer = document.getElementById("analysis-run-container");
    const readyCard = document.getElementById("analysis-ready-card");

    if (!currentRunDir) {
        if (emptyState) emptyState.style.display = "flex";
        if (runContainer) runContainer.style.display = "none";
        if (readyCard) readyCard.style.display = "none";
        configContent.innerHTML = '<p class="placeholder-text">Select a run directory to view config</p>';
        criteriaEditorWrap.innerHTML = '<p class="placeholder-text">Select a run directory to edit criteria</p>';
        reviewDashboard.hidden = true;
        dashboardReviewSelect.innerHTML = "";
        const activeRunName = document.getElementById("active-run-name");
        if (activeRunName) activeRunName.textContent = "No run selected";
        const activeRunPill = document.getElementById("active-run-pill");
        if (activeRunPill) activeRunPill.classList.remove("has-run");
        showPaperOverviewUnselected();
        return;
    }

    if (emptyState) emptyState.style.display = "none";
    if (runContainer) runContainer.style.display = "block";

    // Locate run metadata from allRunsList
    const runInfo = allRunsList.find(r => r.name === currentRunDir) || {
        name: currentRunDir,
        paper_title: null,
        original_filename: "manuscript.pdf",
        run_label: "Run 1 of 1",
        upload_date_str: "Recent",
        final_system_status: "Ready",
        path: currentRunDir,
        sha256: ""
    };

    // Update Paper Identity Hero Header (8.1)
    const origNameEl = document.getElementById("analysis-paper-original-name");
    const titleEl = document.getElementById("analysis-paper-title");
    const runLabelEl = document.getElementById("analysis-paper-run-label");
    const dateEl = document.getElementById("analysis-paper-date");
    const statusEl = document.getElementById("analysis-paper-status");
    const techBody = document.getElementById("analysis-tech-body");
    const activeRunName = document.getElementById("active-run-name");
    const activeRunPill = document.getElementById("active-run-pill");

    if (origNameEl) origNameEl.textContent = runInfo.original_filename || runInfo.safe_filename || "Manuscript";
    if (titleEl) {
        if (runInfo.paper_title && runInfo.paper_title.toLowerCase() !== (runInfo.original_filename || "").toLowerCase()) {
            titleEl.textContent = `Title: ${runInfo.paper_title}`;
            titleEl.style.display = "inline";
        } else {
            titleEl.textContent = "";
            titleEl.style.display = "none";
        }
    }
    if (runLabelEl) runLabelEl.textContent = runInfo.run_label || "Run 1 of 1";
    if (dateEl) dateEl.textContent = runInfo.upload_date_str || "Recently uploaded";
    if (statusEl) {
        statusEl.textContent = runInfo.final_system_status || runInfo.status_desc || "Ready";
        statusEl.className = `identity-badge status-badge ${getStatusBadgeClass(runInfo.final_system_status || runInfo.status)}`;
    }
    if (currentPaperDisplay) {
        currentPaperDisplay.textContent = runInfo.original_filename || runInfo.safe_filename || "-";
    }

    // Populate Ready for Analysis card metadata
    const readyFilename = document.getElementById("ready-card-filename");
    const readyRunLabel = document.getElementById("ready-card-run-label");
    const readyDate = document.getElementById("ready-card-date");
    const readyTitleBox = document.getElementById("ready-card-title-box");
    const readyPaperTitle = document.getElementById("ready-card-paper-title");

    if (readyFilename) readyFilename.textContent = runInfo.original_filename || runInfo.safe_filename || "manuscript.pdf";
    if (readyRunLabel) readyRunLabel.textContent = runInfo.run_label || "Run 1 of 1";
    if (readyDate) readyDate.textContent = runInfo.upload_date_str || "Recently uploaded";
    if (readyTitleBox && readyPaperTitle) {
        if (runInfo.paper_title && runInfo.paper_title.toLowerCase() !== (runInfo.original_filename || "").toLowerCase()) {
            readyTitleBox.style.display = "flex";
            readyPaperTitle.textContent = runInfo.paper_title;
        } else {
            readyTitleBox.style.display = "none";
        }
    }
    if (!runInfo.has_output) {
        if (readyCard) readyCard.style.display = "block";
        reviewDashboard.hidden = true;
    }
    if (techBody) {
        techBody.innerHTML = `
            <div class="tech-row"><span class="tech-label">Directory:</span><code>${escapeHtml(runInfo.name)}</code></div>
            <div class="tech-row"><span class="tech-label">Safe Filename:</span><code>${escapeHtml(runInfo.safe_filename || runInfo.original_filename)}</code></div>
            <div class="tech-row"><span class="tech-label">Upload Timestamp:</span><code>${escapeHtml(runInfo.upload_timestamp || "Unknown")}</code></div>
            <div class="tech-row"><span class="tech-label">SHA-256 Hash:</span><code>${escapeHtml(runInfo.sha256 || "Inferred at runtime")}</code></div>
        `;
    }
    if (activeRunName) {
        activeRunName.textContent = runInfo.display_name || runInfo.paper_title || currentRunDir;
        activeRunName.title = `Directory: ${currentRunDir}`;
    }
    if (activeRunPill) activeRunPill.classList.add("has-run");

    // Sync URL hash
    if (window.location.hash.startsWith("#/analysis")) {
        const expectedHash = `#/analysis/${currentRunDir}`;
        if (window.location.hash !== expectedHash) {
            history.replaceState(null, "", expectedHash);
        }
    } else if (window.location.hash.startsWith("#/paper-overview")) {
        const expectedHash = `#/paper-overview/${currentRunDir}`;
        if (window.location.hash !== expectedHash) {
            history.replaceState(null, "", expectedHash);
        }
        loadPaperOverview(currentRunDir);
    }

    // Load configs and reviews with token verification (Requirement 8.5)
    await loadConfig();
    if (currentToken !== activeLoadToken) return;

    await loadCriteria();
    if (currentToken !== activeLoadToken) return;

    await loadResults();
    if (currentToken !== activeLoadToken) return;

    await loadReviews();
    if (currentToken !== activeLoadToken) return;

    await checkJudgeVerdicts();
}

// ---- Start / Stop ----

async function startRun() {
    if (!currentRunDir) return;

    startBtn.disabled = true;
    const analysisStartBtn = document.getElementById("analysis-start-btn");
    if (analysisStartBtn) analysisStartBtn.disabled = true;
    const readyCard = document.getElementById("analysis-ready-card");
    if (readyCard) readyCard.style.display = "none";
    stopBtn.style.display = "inline-block";
    runStartTime = Date.now();
    logContent.innerHTML = "";
    clearStages();

    try {
        const data = await api("/api/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                run_dir: currentRunDir,
                mode: modeSelector.value,
                config_overrides: {},
            }),
        });
        currentRunId = data.run_id;
        connectSSE();
        startElapsedTimer();
    } catch (e) {
        addLog("Failed to start run: " + e.message, "error");
        startBtn.disabled = false;
        if (analysisStartBtn) analysisStartBtn.disabled = false;
        stopBtn.style.display = "none";
        if (readyCard && reportsData.length === 0) readyCard.style.display = "block";
    }
}

async function stopRun() {
    if (!currentRunId) return;
    try {
        await api(`/api/stop/${currentRunId}`, { method: "POST" });
        addLog("Cancellation requested...", "warning");
    } catch (e) {
        addLog("Stop failed: " + e.message, "error");
    }
}

// ---- SSE ----

let sseReconnectAttempts = 0;
const SSE_MAX_RECONNECT = 5;

function connectSSE() {
    if (eventSource) eventSource.close();
    sseReconnectAttempts = 0;
    _openSSE();
}

function _openSSE() {
    eventSource = new EventSource(`/api/events/${currentRunId}`);

    eventSource.addEventListener("progress", (e) => {
        sseReconnectAttempts = 0;
        try {
            const evt = JSON.parse(e.data);
            handleEvent(evt);
        } catch {}
    });

    eventSource.addEventListener("ping", () => {
        sseReconnectAttempts = 0;
    });

    eventSource.onerror = () => {
        eventSource.close();
        eventSource = null;
        sseReconnectAttempts++;
        if (sseReconnectAttempts <= SSE_MAX_RECONNECT) {
            addLog(`Connection lost — reconnecting (${sseReconnectAttempts}/${SSE_MAX_RECONNECT})...`, "warning");
            setTimeout(_openSSE, 2000 * sseReconnectAttempts);
        } else {
            addLog("Connection lost. Refresh the page to reconnect.", "error");
        }
    };
}

function updateProcessingStudio(evt) {
    const studio = document.getElementById("upload-processing-studio");
    if (!studio) return;

    if (evt.event_type === "run_started") {
        studio.style.display = "block";
        const titleEl = document.getElementById("proc-paper-title");
        if (titleEl) titleEl.textContent = `Analyzing ${evt.paper_count || 1} Manuscript(s)...`;
        setProcStage(1, "completed");
        setProcStage(2, "active");
        setProcProgress(12, "Extracting text and metadata...");
    } else if (evt.event_type === "stage_started") {
        const titleEl = document.getElementById("proc-paper-title");
        if (titleEl && evt.paper_filename) titleEl.textContent = evt.paper_filename;
        const scannerName = document.getElementById("scanner-doc-name");
        if (scannerName && evt.paper_filename) scannerName.textContent = evt.paper_filename;
        const scannerStatus = document.getElementById("scanner-doc-status");

        const stage = evt.stage_name || "";
        if (stage === "Section Analysis") {
            setProcStage(2, "completed");
            setProcStage(3, "active");
            setProcProgress(28, "Mapping academic sections...");
            if (scannerStatus) scannerStatus.textContent = "Detecting structural academic headings...";
        } else if (stage === "Supplemental Analysis") {
            setProcStage(3, "completed");
            setProcStage(4, "active");
            setProcProgress(42, "Auditing methodology & consistency...");
            if (scannerStatus) scannerStatus.textContent = "Cross-section consistency and citation heuristics...";
        } else if (stage === "Librarian" || stage === "Fact-Check") {
            setProcStage(4, "completed");
            setProcStage(5, "active");
            setProcProgress(56, "Verifying evidence quotes in source text...");
            if (scannerStatus) scannerStatus.textContent = "Grounding quotations against original manuscript...";
        } else if (stage === "Extraction") {
            setProcStage(4, "completed");
            setProcStage(5, "completed");
            setProcStage(6, "active");
            setProcProgress(70, "Multi-agent rubric scoring...");
            if (scannerStatus) scannerStatus.textContent = "Extracting criteria findings with local Ollama LLM...";
        } else if (stage === "Synthesis") {
            setProcStage(6, "completed");
            setProcStage(7, "active");
            setProcProgress(86, "Synthesizing final review report...");
            if (scannerStatus) scannerStatus.textContent = "Adjudicating recommendations & safety gates...";
        }
    } else if (evt.event_type === "run_progress") {
        const pct = evt.papers_total > 0 ? (100 * evt.papers_done / evt.papers_total) : 0;
        setProcProgress(Math.max(15, pct));
        const etaEl = document.getElementById("proc-progress-eta");
        if (etaEl && evt.estimated_remaining_s > 0) {
            etaEl.textContent = `ETA: ${formatDuration(evt.estimated_remaining_s)}`;
        }
    } else if (evt.event_type === "paper_completed" || evt.event_type === "run_completed") {
        setProcStage(7, "completed");
        setProcStage(8, "completed");
        setProcProgress(100, "Review report ready");
        const scannerStatus = document.getElementById("scanner-doc-status");
        if (scannerStatus) scannerStatus.textContent = "Evaluation complete. Evidence grounded.";
        const completionCard = document.getElementById("proc-completion-card");
        if (completionCard) {
            completionCard.style.display = "flex";
            const viewBtn = document.getElementById("proc-view-results-btn");
            if (viewBtn) {
                viewBtn.onclick = () => {
                    window.location.hash = `#/analysis/${currentRunDir}`;
                };
            }
        }
    }
}

function setProcStage(num, status) {
    const item = document.getElementById(`proc-stage-${num}`);
    const badge = document.getElementById(`proc-badge-${num}`);
    if (!item) return;

    item.classList.remove("pending", "active", "completed");
    item.classList.add(status);

    if (badge) {
        if (status === "active") badge.textContent = "Running";
        else if (status === "completed") badge.textContent = "Done";
        else badge.textContent = "Pending";
    }
}

function setProcProgress(pct, caption) {
    const fill = document.getElementById("proc-progress-bar-fill");
    const text = document.getElementById("proc-progress-percent");
    if (fill) fill.style.width = `${Math.min(100, pct)}%`;
    if (text) text.textContent = `${Math.round(pct)}% Completed ${caption ? `· ${caption}` : ''}`;
}

function handleEvent(evt) {
    updateProcessingStudio(evt);
    switch (evt.event_type) {
        case "run_started":
            addLog(`Run started: ${evt.mode} mode, ${evt.paper_count} papers`, "info");
            break;

        case "stage_started":
            activateStage(evt.stage_name);
            addLog(`[${evt.stage_name}] Starting for ${evt.paper_filename}`, "info");
            currentPaperDisplay.textContent = evt.paper_filename;
            break;

        case "stage_progress":
            addLog(`[${evt.stage_name}] ${evt.current}/${evt.total} ${evt.detail}`, "info");
            break;

        case "stage_completed":
            completeStage(evt.stage_name);
            addLog(`[${evt.stage_name}] Done in ${evt.duration_s?.toFixed(1)}s - ${evt.result_summary}`, "success");
            break;

        case "paper_completed": {
            const complete = evt.review_status === "complete";
            const level = complete ? "success" : "warning";
            addLog(
                `Paper report: ${evt.paper_filename} | ${evt.successful_criteria}/${evt.total_criteria} criteria | ` +
                `${evt.verified_evidence_count} verified quotes | ${evt.recommendation}`,
                level
            );
            break;
        }

        case "run_progress": {
            const pct = evt.papers_total > 0 ? (100 * evt.papers_done / evt.papers_total) : 0;
            progressBar.style.width = pct + "%";
            progressText.textContent = `${evt.papers_done}/${evt.papers_total} papers (${pct.toFixed(0)}%)`;
            if (evt.estimated_remaining_s > 0) {
                progressEta.textContent = `ETA: ${formatDuration(evt.estimated_remaining_s)}`;
            }
            break;
        }

        case "cost_update":
            costDisplay.textContent = `$${evt.total_cost?.toFixed(4)}`;
            break;

        case "error":
            addLog(`[${evt.stage_name}] ${evt.message}`, evt.recoverable ? "warning" : "error");
            break;

        case "run_completed": {
            const successful = Number(evt.total_papers || 0);
            const total = Number(evt.papers_total || 0);
            const status = evt.status || "completed";
            const complete = status === "completed";
            const pct = total > 0 ? Math.min(99, 100 * successful / total) : 0;
            const label = status.replace(/_/g, " ");
            addLog(
                `Run ${label}: ${successful}/${total} reports, ${evt.papers_failed || 0} failed, ` +
                `$${evt.total_cost?.toFixed(4)}, ${formatDuration(evt.total_time_s)}`,
                complete ? "success" : "warning"
            );
            progressBar.style.width = (complete ? 100 : pct) + "%";
            progressText.textContent = complete
                ? `${successful}/${total} reports · complete`
                : `${successful}/${total} reports · ${label}`;
            progressEta.textContent = "";
            if (!batchActive) finishRun();
            break;
        }

        case "batch_dir_started":
            addLog(`[Batch] Starting ${evt.dir_name} (${evt.dir_index + 1}/${evt.dir_total})`, "info");
            updateBatchProgress(evt.dir_index, evt.dir_total, evt.dir_name);
            fetchBatchQueue();
            break;

        case "batch_completed":
            addLog(`[Batch] Done: ${evt.completed} completed, ${evt.failed} failed, ${formatDuration(evt.total_time_s)}`, "success");
            finishBatch();
            fetchBatchQueue();
            loadRuns();
            loadReviewsList();
            break;

        case "batch_item_started":
            fetchBatchQueue();
            break;

        case "batch_item_completed":
        case "batch_item_failed":
        case "batch_queue_finished":
            fetchBatchQueue();
            loadRuns();
            loadReviewsList();
            break;
    }
}

function finishRun() {
    startBtn.disabled = false;
    const analysisStartBtn = document.getElementById("analysis-start-btn");
    if (analysisStartBtn) analysisStartBtn.disabled = false;
    stopBtn.style.display = "none";
    judgeBtn.disabled = false;
    if (elapsedInterval) clearInterval(elapsedInterval);
    if (eventSource) { eventSource.close(); eventSource = null; }
    loadResults();
    loadRuns();
    checkJudgeVerdicts();
}

// ---- Reports Table ----

let reportsData = [];
let dashboardLoadRevision = 0;

async function loadResults() {
    const readyCard = document.getElementById("analysis-ready-card");
    if (!currentRunDir) {
        reviewDashboard.hidden = true;
        if (readyCard) readyCard.style.display = "none";
        return;
    }
    try {
        const data = await api(`/api/all-reviews/${currentRunDir}`);
        reportsData = data.reviews || [];
        const selectedReport = populateDashboardReviewSelect();
        if (selectedReport) {
            await loadDashboardReview(selectedReport.filename);
            if (readyCard) readyCard.style.display = "none";
        } else {
            reviewDashboard.hidden = true;
            if (readyCard && currentRunDir) readyCard.style.display = "block";
        }
    } catch {
        reportsData = [];
        dashboardReviewSelect.innerHTML = "";
        reviewDashboard.hidden = true;
        if (readyCard && currentRunDir) readyCard.style.display = "block";
    }
}

function populateDashboardReviewSelect() {
    const ordered = [...reportsData].sort((a, b) =>
        String(b.timestamp || b.filename).localeCompare(String(a.timestamp || a.filename))
    );
    const previousFilename = dashboardReviewSelect.value;
    dashboardReviewSelect.innerHTML = ordered.map(report => {
        const name = report.paper || report.filename || "Review report";
        const date = report.display_date ? ` · ${report.display_date}` : "";
        return `<option value="${escapeHtml(report.filename)}">${escapeHtml(name + date)}</option>`;
    }).join("");
    return ordered.find(report => report.filename === previousFilename) || ordered[0] || null;
}

async function loadDashboardReview(filename) {
    const metadata = reportsData.find(report => report.filename === filename);
    if (!metadata || !currentRunDir) return;

    const revision = ++dashboardLoadRevision;
    const runDir = currentRunDir;
    dashboardReviewSelect.value = filename;
    reviewDashboard.hidden = false;

    try {
        const reportResponse = await fetch(
            `/api/review/${encodeURIComponent(runDir)}/${encodeURIComponent(filename)}`
        );
        if (!reportResponse.ok) throw new Error(`Report request failed (${reportResponse.status})`);
        const markdown = await reportResponse.text();
        if (revision !== dashboardLoadRevision || runDir !== currentRunDir) return;

        const originalFilename = markdown.match(/^\*\*(?:Original Filename|Filename):\*\*\s*(.+)$/mi)?.[1]?.trim();
        const paperFilename = originalFilename || (metadata.paper ? `${metadata.paper}.pdf` : "");
        const evidenceRequest = paperFilename
            ? api(`/api/evidence/${encodeURIComponent(runDir)}/${encodeURIComponent(paperFilename)}`)
                .catch(() => ({ evidence: [] }))
            : Promise.resolve({ evidence: [] });
        const [criteriaResult, evidenceResult] = await Promise.all([
            api(`/api/criteria/${encodeURIComponent(runDir)}`).catch(() => ({ criteria: {} })),
            evidenceRequest,
        ]);
        if (revision !== dashboardLoadRevision || runDir !== currentRunDir) return;

        renderDashboardReview(metadata, markdown, criteriaResult.criteria || {}, evidenceResult.evidence || []);
    } catch (error) {
        if (revision !== dashboardLoadRevision || runDir !== currentRunDir) return;
        renderDashboardReview(metadata, "", {}, []);
        const description = document.getElementById("dashboard-state-description");
        description.textContent = `Review details could not be loaded: ${error.message}`;
    }
}

function extractMarkdownSection(markdown, title) {
    const lines = String(markdown || "").split(/\r?\n/);
    const wanted = title.trim().toLowerCase();
    let active = false;
    let sectionLevel = 0;
    const content = [];

    for (const line of lines) {
        const heading = /^(#{1,6})\s+(.+?)\s*$/.exec(line);
        if (heading) {
            const headingTitle = heading[2].replace(/\s+#+$/, "").trim().toLowerCase();
            if (active && heading[1].length <= sectionLevel) break;
            if (!active && headingTitle === wanted) {
                active = true;
                sectionLevel = heading[1].length;
                continue;
            }
        }
        if (active) content.push(line);
    }
    return content.join("\n").trim();
}

function parseMarkdownList(markdown, heading) {
    const headings = Array.isArray(heading) ? heading : [heading];
    let section = "";
    for (const h of headings) {
        section = extractMarkdownSection(markdown, h);
        if (section) break;
    }
    if (!section || /^none\s*$/i.test(section)) return [];
    const items = [];
    let current = "";

    const pushCurrent = () => {
        const value = current.trim();
        if (value && !/^none\s*$/i.test(value)) items.push(value);
        current = "";
    };

    for (const line of section.split(/\r?\n/)) {
        const item = /^\s*(?:[-*+]\s+|\d+\.\s+)(.+)$/.exec(line);
        if (item) {
            pushCurrent();
            current = item[1].trim();
        } else if (current && line.trim() && !line.trim().startsWith("#")) {
            current += ` ${line.trim()}`;
        }
    }
    pushCurrent();

    // If no bullet items were parsed, preserve diagnostic status text (never return empty if status exists)
    if (items.length === 0) {
        const cleanText = section.replace(/^#+.*$/gm, "").trim();
        if (cleanText && !/^none\s*$/i.test(cleanText)) {
            items.push(cleanText);
        }
    }
    return items;
}

function parseCriterionAssessments(markdown) {
    const section = extractMarkdownSection(markdown, "Criterion-by-Criterion Analysis");
    const assessments = [];
    let current = null;
    const finish = () => {
        if (current) {
            current.narrative = current.lines.join(" ").replace(/\s+/g, " ").trim();
            delete current.lines;
            assessments.push(current);
        }
    };

    for (const line of section.split(/\r?\n/)) {
        const heading = /^###\s+(.+?)\s+\(Weight:\s*([\d.]+)%\s*,\s*Score:\s*([\d.]+)\)\s*$/.exec(line.trim());
        if (heading) {
            finish();
            current = {
                id: heading[1].trim(),
                weight: Number(heading[2]),
                score: Number(heading[3]),
                lines: [],
            };
        } else if (current && line.trim()) {
            current.lines.push(line.trim());
        }
    }
    finish();
    return assessments;
}

function parseCriterionIdsFromWarnings(warnings) {
    const ids = new Set();
    for (const warning of warnings) {
        const match = /Fewer than \d+ verified quotations support:\s*(.+?)\.?$/i.exec(warning);
        if (match) {
            match[1].split(",").map(item => item.trim()).filter(Boolean).forEach(id => ids.add(id));
        }
    }
    return ids;
}

function parseCriterionIdsFromErrors(markdown) {
    const ids = new Set();
    for (const line of extractMarkdownSection(markdown, "Criterion Extraction Errors").split(/\r?\n/)) {
        const match = /^\s*-\s+\*\*([^*]+):\*\*/.exec(line);
        if (match) ids.add(match[1].trim());
    }
    return ids;
}

function renderDashboardReview(metadata, markdown, criteriaConfig, evidenceRecords) {
    const status = String(metadata.review_status || "legacy").toLowerCase();
    const statusLabels = {
        complete: "Complete",
        manual_review: "Needs Manual Review",
        incomplete: "Incomplete",
        failed: "Failed",
        legacy: "Legacy report",
    };
    const statusTitle = {
        complete: "Review completed",
        manual_review: "Manual review required",
        incomplete: "Review incomplete",
        failed: "Review could not be completed",
        legacy: "Review metadata unavailable",
    };
    const rawTitle = markdown.match(/^#\s+Review:\s*(.+)$/mi)?.[1]?.trim() || metadata.paper || "Review summary";
    const title = rawTitle.replace(/^\*+|\*+$/g, "").trim();
    const originalFilename = markdown.match(/^\*\*(?:Original Filename|Filename):\*\*\s*(.+)$/mi)?.[1]?.trim();
    const modelNames = [metadata.extractor_model, metadata.synthesizer_model].filter(Boolean);
    const paperDetails = [originalFilename || metadata.paper, ...modelNames, metadata.display_date].filter(Boolean);
    const stateBanner = document.getElementById("dashboard-state-banner");
    const stateIcon = document.getElementById("dashboard-state-icon");
    const recommendationNeedsReview = ["manual_review", "incomplete"].includes(status);
    const finalRecommendation = recommendationNeedsReview
        ? "Needs Manual Review"
        : status === "failed"
            ? "No final recommendation"
            : (metadata.recommendation || "Not recorded");
    const rawProvisional = metadata.provisional_recommendation;
    const provisionalRec = status === "complete"
        ? (rawProvisional || finalRecommendation)
        : rawProvisional
            ? (rawProvisional.toLowerCase().includes("provisional") ? rawProvisional : `${rawProvisional} (Provisional)`)
            : (status === "failed" ? "Unavailable" : `${finalRecommendation} (Provisional)`);

    const runInfo = allRunsList.find(r => r.name === currentRunDir);
    let cleanTitle = (runInfo && runInfo.paper_title && !runInfo.paper_title.toLowerCase().startsWith("keyword")) ? runInfo.paper_title : "";
    if (!cleanTitle) {
        cleanTitle = (title && !title.toLowerCase().startsWith("keyword")) ? title : "";
    }
    if (!cleanTitle) {
        cleanTitle = (runInfo && runInfo.original_filename) || metadata.paper_filename || "Research Manuscript";
    }

    document.getElementById("dashboard-paper-title").textContent = cleanTitle;
    document.getElementById("dashboard-paper-meta").textContent = paperDetails.join(" · ");

    // Update Paper Identity Hero Header (8.1)
    const heroOrigName = document.getElementById("analysis-paper-original-name");
    const heroTitle = document.getElementById("analysis-paper-title");
    const heroRunLabel = document.getElementById("analysis-paper-run-label");
    const heroDate = document.getElementById("analysis-paper-date");
    const heroStatus = document.getElementById("analysis-paper-status");
    if (heroOrigName) {
        heroOrigName.textContent = (runInfo && runInfo.original_filename) || originalFilename || metadata.paper_filename || cleanTitle;
    }
    if (heroTitle) {
        heroTitle.textContent = cleanTitle;
    }
    if (heroRunLabel) {
        heroRunLabel.textContent = (runInfo && runInfo.run_label) || "Run 1 of 1";
    }
    if (heroDate) {
        heroDate.textContent = (runInfo && runInfo.upload_date_str) || metadata.display_date || "Recent";
    }
    if (heroStatus) {
        heroStatus.textContent = finalRecommendation;
        heroStatus.className = `identity-badge status-badge ${getStatusBadgeClass(finalRecommendation)}`;
    }

    // Update Progress Panel for loaded review
    const progressBar = document.getElementById("progress-bar");
    const progressText = document.getElementById("progress-text");
    const costDisplay = document.getElementById("cost-display");
    const currentPaperDisplay = document.getElementById("current-paper-display");
    if (progressBar) progressBar.style.width = "100%";
    if (progressText) progressText.textContent = "Review complete · 100%";
    if (costDisplay) {
        costDisplay.textContent = metadata.total_cost_usd != null
            ? `$${parseFloat(metadata.total_cost_usd).toFixed(4)}`
            : "$0.00";
    }
    if (currentPaperDisplay) {
        currentPaperDisplay.textContent = (runInfo && runInfo.original_filename) || originalFilename || metadata.paper_filename || title;
    }
    ["Ingestion", "Extraction", "Synthesis", "Output"].forEach(completeStage);

    stateBanner.className = `dashboard-state-banner state-${status}`;
    stateIcon.innerHTML = status === "complete"
        ? '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>'
        : status === "legacy"
            ? '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>'
            : '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>';
    document.getElementById("dashboard-state-title").textContent = statusTitle[status] || "Review status";

    const successful = Number(metadata.successful_criteria);
    const totalCriteria = Number(metadata.total_criteria);
    const failed = Number(metadata.failed_criteria);
    const coverageKnown = Number.isFinite(successful) && Number.isFinite(totalCriteria) && totalCriteria > 0;
    const stateDescription = status === "manual_review"
        ? `${coverageKnown ? `${successful}/${totalCriteria} criteria completed. ` : ""}The final recommendation is withheld because verified evidence coverage is insufficient.`
        : status === "incomplete"
            ? `${coverageKnown ? `${successful}/${totalCriteria} criteria completed. ` : ""}Review the extraction warnings before relying on the provisional score.`
            : status === "failed"
                ? "The synthesis or review output did not pass validation. Use the report details for diagnosis."
                : status === "legacy"
                    ? "This report predates the current coverage metadata. Review its source report before relying on the recommendation."
                    : "All configured criteria and evidence checks are recorded for this review.";
    document.getElementById("dashboard-state-description").textContent = stateDescription;

    const score = Number(metadata.overall_score);
    const scoreKnown = metadata.overall_score !== null && metadata.overall_score !== undefined && Number.isFinite(score);
    document.getElementById("dashboard-score").textContent = scoreKnown ? `${score.toFixed(1)} / 100` : "—";
    document.getElementById("dashboard-donut-score").textContent = scoreKnown ? score.toFixed(1) : "—";
    document.getElementById("dashboard-score-fill").style.width = scoreKnown ? `${Math.max(0, Math.min(100, score))}%` : "0%";
    if (typeof updateRadialScoreGauge === "function") {
        updateRadialScoreGauge(scoreKnown ? score : null);
    }
    document.getElementById("dashboard-recommendation").textContent = provisionalRec;
    document.getElementById("dashboard-provisional").textContent = metadata.provisional_recommendation
        ? (status === "complete"
            ? "Verified algorithmic recommendation · Passed safety gate"
            : "Provisional model proposal · Intercepted by verification safety gate")
        : (status === "complete" ? "Verified algorithmic recommendation" : "");
    document.getElementById("dashboard-status").textContent = statusLabels[status] || status.replace(/_/g, " ");
    document.getElementById("dashboard-status-detail").textContent = coverageKnown
        ? `${successful}/${totalCriteria} successful · ${Number.isFinite(failed) ? failed : totalCriteria - successful} failed`
        : "Coverage metadata unavailable";

    const statusCard = document.querySelector(".status-metric-card");
    if (statusCard) {
        statusCard.dataset.reviewStatus = status;
    }
    const recCard = document.querySelector(".recommendation-metric-card");
    if (recCard) {
        const recText = String(metadata.provisional_recommendation || finalRecommendation || "").toLowerCase();
        const recCategory = (status === "complete")
            ? (recText.includes("accept") ? "accept" : recText.includes("reject") ? "reject" : "review")
            : (status === "failed" ? "reject" : "review");
        recCard.dataset.recCategory = recCategory;
    }

    const warnings = parseMarkdownList(markdown, "Warnings");
    const warningsSection = document.getElementById("dashboard-warnings");
    const warningList = document.getElementById("dashboard-warning-list");
    warningsSection.hidden = warnings.length === 0;
    warningList.innerHTML = warnings.map(warning => `<li>${escapeHtml(warning)}</li>`).join("");

    const executiveSummary = extractMarkdownSection(markdown, "Executive Summary");
    const executiveCard = document.getElementById("dashboard-executive-card");
    executiveCard.hidden = !executiveSummary;
    const execSummaryEl = document.getElementById("dashboard-executive-summary");
    if (execSummaryEl) {
        execSummaryEl.innerHTML = formatEvidenceCitations(escapeHtml(executiveSummary));
    }

    const verifiedRecords = evidenceRecords.filter(item => String(item.verification_status || "").toLowerCase() === "verified").length;
    const evidenceRecordsKnown = evidenceRecords.length > 0
        || (metadata.verified_evidence_count !== null && metadata.verified_evidence_count !== undefined)
        || (metadata.unverified_evidence_count !== null && metadata.unverified_evidence_count !== undefined);
    const verifiedCount = evidenceRecords.length > 0
        ? verifiedRecords
        : Number(metadata.verified_evidence_count || 0);
    const unverifiedCount = evidenceRecords.length > 0
        ? evidenceRecords.length - verifiedRecords
        : Number(metadata.unverified_evidence_count || 0);
    const evidenceTotal = verifiedCount + unverifiedCount;
    const verifiedPercent = evidenceTotal ? Math.round((verifiedCount / evidenceTotal) * 100) : 0;
    document.getElementById("dashboard-evidence-count").textContent = evidenceRecordsKnown
        ? `${verifiedCount} / ${evidenceTotal}`
        : "—";
    document.getElementById("dashboard-evidence-caption").textContent = evidenceRecordsKnown
        ? `${unverifiedCount} unverified · ${evidenceTotal} total quotations`
        : "Evidence counts are not recorded";
    document.getElementById("dashboard-evidence-percent").textContent = evidenceRecordsKnown && evidenceTotal
        ? `${verifiedPercent}% verified`
        : "";
    document.getElementById("dashboard-verified-count").textContent = evidenceRecordsKnown ? verifiedCount : "—";
    document.getElementById("dashboard-unverified-count").textContent = evidenceRecordsKnown ? unverifiedCount : "—";
    const verifiedWidth = evidenceTotal ? `${verifiedCount / evidenceTotal * 100}%` : "0%";
    const unverifiedWidth = evidenceTotal ? `${unverifiedCount / evidenceTotal * 100}%` : "0%";
    document.getElementById("dashboard-verified-meter").style.width = verifiedWidth;
    document.getElementById("dashboard-unverified-meter").style.width = unverifiedWidth;
    document.getElementById("dashboard-verified-chart").style.width = verifiedWidth;
    document.getElementById("dashboard-unverified-chart").style.width = unverifiedWidth;

    const criteria = Array.isArray(criteriaConfig.criteria) ? criteriaConfig.criteria : [];
    const assessments = parseCriterionAssessments(markdown);
    const assessmentById = new Map(assessments.map(item => [item.id, item]));
    const evidenceById = new Map();
    for (const item of evidenceRecords) {
        const criterionId = item.criterion_id || "";
        if (!evidenceById.has(criterionId)) evidenceById.set(criterionId, []);
        evidenceById.get(criterionId).push(item);
    }
    const insufficientIds = parseCriterionIdsFromWarnings(warnings);
    const extractionErrorIds = parseCriterionIdsFromErrors(markdown);
    const definitions = criteria.length
        ? criteria
        : assessments.map(item => ({ id: item.id, name: item.id, weight: item.weight }));
    const rows = definitions.map(definition => {
        const id = definition.id || "";
        const assessment = assessmentById.get(id)
            || assessments.find(item => item.id === definition.name);
        const score = assessment && Number.isFinite(assessment.score) ? assessment.score : null;
        const weight = Number(definition.weight ?? assessment?.weight);
        const range = definition.scale?.range;
        const maximum = Array.isArray(range) && Number.isFinite(Number(range[1])) ? Number(range[1]) : null;
        const records = evidenceById.get(id) || [];
        const verified = records.filter(item => String(item.verification_status || "").toLowerCase() === "verified").length;
        const unverified = records.length - verified;
        const statusLabel = extractionErrorIds.has(id)
            ? "Extraction failed"
            : insufficientIds.has(id)
                ? "Needs more evidence"
                : unverified > 0
                    ? "Review unverified quote"
                    : verified > 0
                        ? "Quote verified in source"
                        : "No quotations recorded";
        return {
            id,
            name: definition.name || id,
            score,
            weight: Number.isFinite(weight) ? weight : null,
            maximum,
            verified,
            unverified,
            total: records.length,
            statusLabel,
            assessment: assessment?.narrative || "No criterion narrative is available in this report.",
        };
    });
    renderDashboardCriteria(rows, assessments.length > 0, metadata);
    renderDashboardScoreBreakdown(rows, scoreKnown ? score : null);

    const strengths = parseMarkdownList(markdown, ["Major Strengths", "Key Strengths"]);
    const concerns = [
        ...parseMarkdownList(markdown, ["Major Concerns", "Key Weaknesses"]),
        ...parseMarkdownList(markdown, ["Minor Issues"]),
    ];
    const suggestions = parseMarkdownList(markdown, ["Revision Suggestions", "Actionable Suggestions", "Suggestions"]);
    renderDashboardList("dashboard-strengths-card", "dashboard-strengths-list", strengths);
    renderDashboardList("dashboard-concerns-card", "dashboard-concerns-list", concerns);
    renderDashboardList("dashboard-suggestions-card", "dashboard-suggestions-list", suggestions, true);
}

function renderDashboardCriteria(rows, hasAssessments, metadata) {
    const tableBody = document.getElementById("dashboard-criteria-body");
    const emptyMessage = document.getElementById("dashboard-criteria-empty");
    const countLabel = document.getElementById("dashboard-criteria-count");
    tableBody.innerHTML = "";
    emptyMessage.hidden = rows.length > 0 && hasAssessments;
    if (!rows.length || !hasAssessments) {
        countLabel.textContent = metadata.total_criteria != null
            ? `${metadata.successful_criteria ?? 0}/${metadata.total_criteria} criteria recorded`
            : "";
        return;
    }

    countLabel.textContent = metadata.total_criteria != null
        ? `${metadata.successful_criteria}/${metadata.total_criteria} criteria · ${metadata.failed_criteria || 0} failed`
        : `${rows.length} criteria`;
    tableBody.innerHTML = rows.map((row, index) => {
        const scoreLabel = row.score === null
            ? "—"
            : row.maximum === null ? String(row.score) : `${row.score} / ${row.maximum}`;
        const scorePercent = row.score !== null && row.maximum
            ? Math.max(0, Math.min(100, (row.score / row.maximum) * 100))
            : 0;
        const scoreTier = scorePercent >= 75 ? "tier-high" : scorePercent >= 50 ? "tier-mid" : "tier-low";
        const weightLabel = row.weight === null ? "—" : `${row.weight}% wt`;

        const isVerified = row.statusLabel === "Quote verified in source" || row.statusLabel === "Evidence verified";
        const isPartial = row.statusLabel === "Review unverified quote" || row.statusLabel === "Unverified quote flagged";
        const isFailed = row.statusLabel === "Extraction failed";

        const statusClass = isVerified
            ? "criterion-status-supported"
            : isPartial
                ? "criterion-status-partial"
                : isFailed
                    ? "criterion-status-failed"
                    : "criterion-status-warning";

        const evidenceSubtext = row.total === 0
            ? "No quotes cited"
            : row.unverified > 0
                ? `${row.unverified} unverified`
                : "Source-verified";

        const evidenceBadgeClass = row.verified > 0 && row.unverified === 0
            ? "evidence-badge-verified"
            : row.unverified > 0
                ? "evidence-badge-warning"
                : "evidence-badge-muted";

        // Highlight inline [Evidence: ...] tags safely
        const escapedAssessment = escapeHtml(row.assessment);
        const formattedAssessment = escapedAssessment.replace(
            /\[Evidence:\s*([^\]]+)\]/g,
            '<span class="evidence-tag" title="Verified source quote citation">$1</span>'
        );

        return `<tr>
            <td class="criterion-index"><span>${index + 1}</span></td>
            <td class="criterion-name-cell">
                <strong class="criterion-title">${escapeHtml(row.name)}</strong>
                <code class="criterion-slug">${escapeHtml(row.id)}</code>
            </td>
            <td class="criterion-score-cell">
                <div class="criterion-score-header">
                    <strong class="criterion-score-num ${scoreTier}">${escapeHtml(scoreLabel)}</strong>
                    <span class="criterion-weight-tag">${escapeHtml(weightLabel)}</span>
                </div>
                <div class="criterion-score-track" aria-hidden="true">
                    <span class="criterion-score-fill ${scoreTier}" style="width:${scorePercent}%"></span>
                </div>
            </td>
            <td class="criterion-evidence-count">
                <div class="criterion-evidence-badge ${evidenceBadgeClass}">
                    <strong class="evidence-count-num">${row.verified} / ${row.total}</strong>
                </div>
                <small class="evidence-count-sub">${escapeHtml(evidenceSubtext)}</small>
            </td>
            <td class="criterion-assessment">${formattedAssessment}</td>
            <td class="criterion-status-cell">
                <span class="criterion-status ${statusClass}">
                    <span class="status-dot-mini" aria-hidden="true"></span>
                    ${escapeHtml(row.statusLabel)}
                </span>
            </td>
        </tr>`;
    }).join("");
}

function renderDashboardScoreBreakdown(rows, overallScore) {
    const content = document.getElementById("dashboard-score-breakdown");
    const empty = document.getElementById("dashboard-score-breakdown-empty");
    const legend = document.getElementById("dashboard-score-legend");
    const chartColors = ["#4388ff", "#2dd4bf", "#a78bfa", "#f6b84b", "#f472b6", "#60a5fa", "#34d399", "#fb7185"];
    const items = rows.map(row => ({
        ...row,
        points: row.score !== null && row.maximum && row.weight !== null
            ? (row.score / row.maximum) * row.weight
            : null,
    })).filter(row => row.points !== null && row.points >= 0);
    const totalPoints = items.reduce((sum, item) => sum + item.points, 0);

    if (!items.length || totalPoints <= 0) {
        content.hidden = true;
        empty.hidden = false;
        legend.innerHTML = "";
        return;
    }

    content.hidden = false;
    empty.hidden = true;
    let cursor = 0;
    const segments = items.map((item, index) => {
        const start = cursor;
        cursor += item.points / totalPoints * 100;
        return `${chartColors[index % chartColors.length]} ${start.toFixed(2)}% ${cursor.toFixed(2)}%`;
    });
    const donut = document.getElementById("dashboard-score-donut");
    donut.style.background = `conic-gradient(${segments.join(", ")})`;
    donut.setAttribute("aria-label", `Weighted score contributions total ${totalPoints.toFixed(1)} points`);
    document.getElementById("dashboard-donut-score").textContent = overallScore === null
        ? totalPoints.toFixed(1)
        : overallScore.toFixed(1);
    legend.innerHTML = items.map((item, index) => {
        const scoreLabel = item.maximum === null ? String(item.score) : `${item.score}/${item.maximum}`;
        const weightLabel = item.weight === null ? "weight unavailable" : `${item.weight}% weight`;
        return `<li><span class="score-legend-dot" style="--legend-color:${chartColors[index % chartColors.length]}"></span><span class="score-legend-name">${escapeHtml(item.name)}<small>${escapeHtml(scoreLabel)} · ${escapeHtml(weightLabel)}</small></span><strong>${item.points.toFixed(1)}</strong></li>`;
    }).join("");
}

function renderDashboardList(cardId, listId, items, ordered = false) {
    const card = document.getElementById(cardId);
    const list = document.getElementById(listId);
    if (!card || !list) return;
    card.hidden = items.length === 0;
    list.innerHTML = items.map(item => {
        const trimmed = (item || "").trim();
        if (trimmed.startsWith("*(Status:") || trimmed.includes("(Status:")) {
            const cleanedStatus = formatEvidenceCitations(escapeHtml(trimmed));
            return `<li class="diagnostic-status-item">${cleanedStatus}</li>`;
        }
        let formatted = escapeHtml(trimmed);
        formatted = formatted.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
        formatted = formatted.replace(/\*(.+?)\*/g, "<em>$1</em>");
        formatted = formatEvidenceCitations(formatted);
        return `<li>${formatted}</li>`;
    }).join("");
    if (ordered) list.setAttribute("aria-label", "Revision suggestions");
}

async function loadReviews() {
    // Reviews are loaded on demand when clicking "View"
}

function sortAndRenderResults() {
    // Deprecated legacy table renderer: Past Reviews archive table is maintained exclusively by renderReviewsArchive()
}

function renderResults(results) {
    // Deprecated legacy table renderer: Past Reviews archive table is maintained exclusively by renderReviewsArchive()
}

let activeReviewFilename = "";
let lastModalFocusedElement = null;

function switchModalSection(targetId) {
    const allPanels = [
        "review-modal-body",
        "evidence-panel",
        "section-analysis-panel",
        "quality-analysis-panel",
        "audit-provenance-panel"
    ];

    const pills = document.querySelectorAll(".modal-nav-pill");
    pills.forEach(pill => {
        if (pill.dataset.target === targetId) {
            pill.classList.add("active");
            pill.setAttribute("aria-selected", "true");
        } else {
            pill.classList.remove("active");
            pill.setAttribute("aria-selected", "false");
        }
    });

    allPanels.forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        if (id === targetId) {
            el.style.display = "block";
            el.classList.add("active");
            el.removeAttribute("hidden");
            el.scrollTop = 0;
            el.classList.remove("section-target-highlight");
            void el.offsetWidth;
            el.classList.add("section-target-highlight");
        } else {
            el.style.display = "none";
            el.classList.remove("active");
            el.setAttribute("hidden", "true");
        }
    });
}

function wireModalSubnav() {
    const modalNavPills = document.querySelectorAll(".modal-nav-pill");
    modalNavPills.forEach(pill => {
        pill.onclick = (e) => {
            e.preventDefault();
            const targetId = pill.dataset.target;
            if (targetId) {
                switchModalSection(targetId);
            }
        };
    });
}

async function showReviewByFilename(filename, targetSectionId = "review-modal-body") {
    if (!currentRunDir) return;
    lastModalFocusedElement = document.activeElement;
    activeReviewFilename = filename;
    const paperName = filename.split("_20")[0] || filename;
    reviewModalTitle.textContent = `Review: ${paperName}`;
    reviewModalBody.innerHTML = "Loading...";
    const evidenceList = document.getElementById("evidence-list");
    const evidenceSummary = document.getElementById("evidence-summary");

    if (evidenceList) evidenceList.innerHTML = '<p class="placeholder-text">Loading evidence...</p>';
    if (evidenceSummary) evidenceSummary.textContent = "Loading...";

    try {
        const res = await fetch(`/api/review/${encodeURIComponent(currentRunDir)}/${encodeURIComponent(filename)}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const text = await res.text();

        const runInfo = allRunsList.find(r => r.name === currentRunDir);
        const titleMatch = text.match(/^#\s+(?:Review:\s*)?([^\r\n]+)/m);
        let displayTitle = (runInfo && runInfo.paper_title) || "";
        if (!displayTitle && titleMatch && titleMatch[1].trim() && !titleMatch[1].toLowerCase().includes("keywords")) {
            displayTitle = titleMatch[1].trim();
        }
        if (!displayTitle) {
            displayTitle = (runInfo && runInfo.original_filename) || paperName.replace(/_/g, " ");
        }
        reviewModalTitle.textContent = `Review: ${displayTitle}`;
        reviewModalTitle.title = displayTitle;

        reviewModalBody.innerHTML = renderMarkdown(text);
        reviewModal.style.display = "flex";
        wireModalSubnav();
        switchModalSection(targetSectionId);

        // Make evidence citations inside report body clickable to jump to Grounding Matrix
        reviewModalBody.querySelectorAll(".evidence-cite-badge").forEach(badge => {
            badge.addEventListener("click", (e) => {
                e.stopPropagation();
                const rawCit = badge.dataset.citation || badge.textContent || "";
                const cleanCit = rawCit.replace(/^Evidence:\s*/i, "").trim();
                switchModalSection("evidence-panel");
                const card = document.getElementById(`evidence-card-${cleanCit}`)
                    || Array.from(document.querySelectorAll("#evidence-list .evidence-card")).find(c =>
                        c.textContent.includes(cleanCit)
                    );
                if (card) {
                    card.scrollIntoView({ behavior: "smooth", block: "center" });
                    card.classList.remove("section-target-highlight");
                    void card.offsetWidth;
                    card.classList.add("section-target-highlight");
                }
            });
        });

        await Promise.all([
            loadEvidenceForReview(filename),
            loadSectionAnalysis(filename),
            loadQualityAnalysisForReview(filename),
            loadAuditProvenanceForReview(filename, text),
        ]);
    } catch (e) {
        reviewModalBody.textContent = "Error loading review: " + e.message;
        if (evidenceList) evidenceList.innerHTML = '<p class="placeholder-text">Could not load review evidence.</p>';
        if (evidenceSummary) evidenceSummary.textContent = "Unavailable";
    }
}


function loadAuditProvenanceForReview(filename, text) {
    const modelsVal = document.getElementById("audit-models-val");
    const costVal = document.getElementById("audit-cost-val");
    const timeVal = document.getElementById("audit-time-val");
    const statusVal = document.getElementById("audit-status-val");
    const recMeta = document.getElementById("audit-rec-meta");
    const evidenceVal = document.getElementById("audit-evidence-val");
    const artifactPaths = document.getElementById("audit-artifact-paths");

    const paperStem = filename.split("_20")[0] || filename.replace(".md", "");

    // Clean text by stripping markdown bolding (**) for robust pattern matching
    const cleanText = (text || "").replace(/\*\*/g, "");

    // Models display from review metadata
    const extMatch = cleanText.match(/(?:Extractor Model:\s*)([^\n\r]+)/i);
    const synMatch = cleanText.match(/(?:Synthesizer Model:\s*)([^\n\r]+)/i);
    let modelsDisplay = "—";
    if (extMatch && synMatch) {
        modelsDisplay = `Extractor: ${extMatch[1].trim()} · Synthesizer: ${synMatch[1].trim()}`;
    } else if (extMatch) {
        modelsDisplay = extMatch[1].trim();
    } else {
        const provMatch = cleanText.match(/ollama|llama|gpt|claude|gemini/i);
        modelsDisplay = provMatch ? provMatch[0] : "Local inference";
    }

    // Cost display
    let costDisplayStr = "$0.0000 USD (Local AI)";
    const costMatch = cleanText.match(/(?:Total\s+)?(?:API\s+)?Cost:.*?[\$]?([\d.]+)/i);
    if (costMatch) {
        costDisplayStr = `$${parseFloat(costMatch[1]).toFixed(4)} USD`;
    }

    // Timestamp display
    const dateMatch = cleanText.match(/Review Date:\s*([^\n\r]+)/i);
    const timeDisplay = dateMatch
        ? dateMatch[1].trim()
        : new Date().toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });

    // Status and Recommendation display
    const finalRecMatch = cleanText.match(/Final System Recommendation:\s*([^\r\n]+)/i);
    const provRecMatch = cleanText.match(/Provisional Recommendation:\s*([^\r\n]+)/i);
    const statusMatch = cleanText.match(/Review Status:\s*([A-Za-z_]+)/i);

    const finalRec = finalRecMatch ? finalRecMatch[1].trim() : "";
    const provRec = provRecMatch ? provRecMatch[1].trim() : "";
    const reviewStatus = statusMatch ? statusMatch[1].trim().toLowerCase() : "";

    let statusHtml = '<span class="status-badge status-manual-review">Needs Manual Review</span>';
    let recDetail = provRec
        ? `Provisional: ${provRec} · Intercepted by Safety Gate`
        : "Intercepted by Safety Gate";

    if (reviewStatus === "complete" || finalRec.toUpperCase() === "ACCEPT") {
        statusHtml = '<span class="status-badge status-complete">Accept</span>';
        recDetail = "Approved by safety gate";
    } else if (finalRec.toUpperCase() === "REJECT" && reviewStatus !== "manual_review") {
        statusHtml = '<span class="status-badge status-failed">Reject</span>';
        recDetail = "Rejected by safety gate";
    } else if (reviewStatus === "failed") {
        statusHtml = '<span class="status-badge status-failed">Failed</span>';
        recDetail = provRec ? `Provisional: ${provRec} · Execution failed` : "Failed schema validation";
    }

    // Evidence counts - parsed from actual markdown content
    let evCountDisplay = "—";
    const verMatch = cleanText.match(/Verified Evidence:\s*(\d+)/i);
    const unverMatch = cleanText.match(/Unverified Evidence:\s*(\d+)/i);
    if (verMatch) {
        const verCount = verMatch[1];
        const unverCount = unverMatch ? unverMatch[1] : 0;
        evCountDisplay = `${verCount} Verified · ${unverCount} Unverified`;
    }

    if (modelsVal) modelsVal.textContent = modelsDisplay;
    if (costVal) costVal.textContent = costDisplayStr;
    if (timeVal) timeVal.textContent = timeDisplay;
    if (statusVal) statusVal.innerHTML = statusHtml;
    if (recMeta) recMeta.textContent = recDetail;
    if (evidenceVal) evidenceVal.textContent = evCountDisplay;

    if (artifactPaths) {
        artifactPaths.innerHTML = `
            <div class="artifact-path-row">
                <span class="artifact-type-badge badge-md">Markdown</span>
                <code>outputs/reviews/${escapeHtml(filename)}</code>
            </div>
            <div class="artifact-path-row">
                <span class="artifact-type-badge badge-json">Evidence</span>
                <code>outputs/evidence/${escapeHtml(paperStem)}_evidence.json</code>
            </div>
            <div class="artifact-path-row">
                <span class="artifact-type-badge badge-json">Sections</span>
                <code>outputs/evidence/${escapeHtml(paperStem)}_section_analysis.json</code>
            </div>
            <div class="artifact-path-row">
                <span class="artifact-type-badge badge-csv">CSV Report</span>
                <code>outputs/reports/consolidated_reviews_*.csv</code>
            </div>
        `;
    }
}


async function exportCurrentPdf(targetFilename = null) {
    const fn = targetFilename || activeReviewFilename || (dashboardReviewSelect ? dashboardReviewSelect.value : null);
    if (!currentRunDir || !fn) {
        alert("Please select a review report to export.");
        return;
    }
    const pdfFilename = fn.replace(/\.md$/, ".pdf");
    try {
        const res = await fetch(`/api/export/pdf/${currentRunDir}/${fn}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = pdfFilename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        addLog(`Exported PDF report: ${pdfFilename}`, "success");
    } catch (e) {
        addLog(`Failed to export PDF: ${e.message}`, "error");
    }
}


async function exportCurrentMarkdown(targetFilename = null) {
    const fn = targetFilename || activeReviewFilename || (dashboardReviewSelect ? dashboardReviewSelect.value : null);
    if (!currentRunDir || !fn) {
        alert("Please select a review report to export.");
        return;
    }
    try {
        const res = await fetch(`/api/review/${currentRunDir}/${fn}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = fn;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        addLog(`Exported Markdown report: ${fn}`, "success");
    } catch (e) {
        addLog(`Failed to export Markdown: ${e.message}`, "error");
    }
}


async function exportCurrentAuditJson(targetFilename = null) {
    const fn = targetFilename || activeReviewFilename || (dashboardReviewSelect ? dashboardReviewSelect.value : null);
    if (!currentRunDir || !fn) {
        alert("Please select a review report to export.");
        return;
    }
    try {
        const res = await fetch(`/api/export/audit/${currentRunDir}/${fn}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        const stem = fn.replace(/\.md$/, "");
        a.download = `audit_${stem}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        addLog(`Exported Audit JSON package: audit_${stem}.json`, "success");
    } catch (e) {
        addLog(`Failed to export Audit JSON: ${e.message}`, "error");
    }
}


async function exportConsolidatedCsv() {
    const isReviewsArchivePage = (window.location.hash === "#/reviews" || !currentRunDir);
    const endpoint = isReviewsArchivePage ? "/api/reports/csv" : `/api/reports/csv/${encodeURIComponent(currentRunDir)}`;
    try {
        const res = await fetch(endpoint);
        if (!res.ok) {
            const errDetail = await res.text();
            throw new Error(`Server returned HTTP ${res.status}: ${errDetail || res.statusText}`);
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        const cd = res.headers.get("content-disposition");
        let fname = isReviewsArchivePage ? `review_archive_${new Date().toISOString().slice(0, 10)}.csv` : `consolidated_reviews_${currentRunDir}.csv`;
        if (cd && cd.includes("filename=")) {
            const m = cd.match(/filename="?([^"]+)"?/);
            if (m) fname = m[1];
        }
        a.download = fname;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        addLog(`Exported CSV report: ${fname}`, "success");
    } catch (e) {
        addLog(`Failed to export CSV: ${e.message}`, "error");
        alert("Failed to export CSV: " + e.message);
    }
}


function printCurrentReview() {
    window.print();
}



async function loadSectionAnalysis(reviewFilename) {
    const list = document.getElementById("section-analysis-list");
    const summary = document.getElementById("section-analysis-summary");
    if (!list || !summary) return;

    list.innerHTML = '<p class="placeholder-text">Loading section analysis...</p>';
    summary.textContent = "Loading...";

    try {
        const response = await fetch(
            `/api/section-analysis-for-review/${encodeURIComponent(currentRunDir)}/${encodeURIComponent(reviewFilename)}`
        );

        if (!response.ok) {
            throw new Error(`Section analysis request failed (${response.status})`);
        }

        const report = await response.json();

        if (!report.available) {
            list.innerHTML = '<p class="placeholder-text">Section analysis is not available for this paper yet.</p>';
            summary.textContent = "Not available";
            return;
        }

        const sections = report.sections || {};
        const entries = Object.entries(sections);
        const presentCount = entries.filter(([, item]) => item.status === "present").length;
        const totalCount = entries.length;
        summary.textContent = `${presentCount} Detected · ${totalCount - presentCount} Uncertain · ${totalCount} Checked`;

        list.innerHTML = `
            <div class="section-cards-grid">
                ${entries.map(([name, item]) => {
                    const isPresent = item.status === "present";
                    const statusClass = isPresent ? "section-badge-detected" : "section-badge-uncertain";
                    const statusText = isPresent ? "Heading Detected" : "Heading Not Detected";
                    const formattedName = name.replace(/_/g, " ").replace(/\b\w/g, l => l.toUpperCase());

                    let contentHtml = "";
                    if (item.heading) {
                        contentHtml = `
                            <div class="section-detected-block">
                                <span class="section-meta-label">Matched Heading in Text:</span>
                                <code class="section-matched-code">${escapeHtml(item.heading.replace(/\*\*/g, ""))}</code>
                            </div>
                        `;
                    } else if (item.title_candidate) {
                        contentHtml = `
                            <div class="section-detected-block">
                                <span class="section-meta-label">Title Page Candidate:</span>
                                <strong class="section-title-candidate">${escapeHtml(item.title_candidate.replace(/\*\*/g, ""))}</strong>
                            </div>
                        `;
                    } else {
                        contentHtml = `
                            <div class="section-uncertain-block">
                                <p class="section-uncertain-text">
                                    No explicit heading detected. Content may be integrated into adjacent sections or use alternative conventions.
                                </p>
                            </div>
                        `;
                    }

                    return `
                        <article class="section-card ${isPresent ? "card-detected" : "card-uncertain"}">
                            <div class="section-card-header">
                                <strong class="section-card-title">${escapeHtml(formattedName)}</strong>
                                <span class="section-badge ${statusClass}">
                                    <span class="status-dot"></span>
                                    ${statusText}
                                </span>
                            </div>
                            <div class="section-card-body">
                                ${contentHtml}
                            </div>
                        </article>
                    `;
                }).join("")}
            </div>
        `;
    } catch (error) {
        list.innerHTML = '<p class="placeholder-text">Section analysis could not be loaded.</p>';
        summary.textContent = "Load failed";
        console.error("Section analysis loading error:", error);
    }
}

async function loadQualityAnalysisForReview(reviewFilename) {
    const list = document.getElementById("quality-analysis-list");
    const summary = document.getElementById("quality-analysis-summary");
    if (!list || !summary) return;

    list.innerHTML = '<p class="placeholder-text">Loading supplemental quality analysis...</p>';
    summary.textContent = "Loading...";

    try {
        const response = await fetch(
            `/api/quality-analysis-for-review/${encodeURIComponent(currentRunDir)}/${encodeURIComponent(reviewFilename)}`
        );
        if (!response.ok) {
            throw new Error(`Quality analysis request failed (${response.status})`);
        }

        const payload = await response.json();
        const analyses = payload.analyses || {};
        const labels = {
            citation_reference: "Citations & Reference Analysis",
            document_structure: "Figures, Tables & Equations",
            cross_section_consistency: "Cross-Section Numerical Consistency",
        };
        const entries = Object.entries(labels);
        const completed = entries.filter(([key]) =>
            analyses[key] && analyses[key].available
        ).length;

        if (!payload.available) {
            list.innerHTML = '<p class="placeholder-text">Supplemental analysis is not available for this paper yet. Run a new review to generate these reports.</p>';
            summary.textContent = "Not available";
            return;
        }

        summary.textContent = `${completed} of ${entries.length} checks available`;

        const esc = value => escapeHtml(String(value ?? ""));

        const cards = entries.map(([key, label]) => {
            const report = analyses[key];
            if (!report || !report.available) {
                const status = report?.status === "unreadable"
                    ? "Report could not be read"
                    : "Not generated";
                return `
                    <article class="quality-card quality-unavailable">
                        <div class="quality-card-header">
                            <strong class="quality-card-title">${label}</strong>
                            <span class="quality-badge status-unavailable">${status}</span>
                        </div>
                    </article>
                `;
            }

            let cardContent = "";

            if (key === "citation_reference") {
                const inTextCount = (report.in_text_citation_numbers || []).length;
                const refsCount = (report.references || []).length;
                const unmatched = (report.citations_without_reference || []).length;
                const uncited = (report.references_not_cited || []).length;
                const dups = (report.duplicate_reference_numbers || []).length;
                const incomplete = (report.potentially_incomplete_references || []).length;
                const citationFindings = Array.isArray(report.findings) ? report.findings : [];
                const totalIssues = unmatched + uncited + dups + incomplete + citationFindings.length;

                const statusBadge = totalIssues === 0
                    ? `<span class="quality-badge status-verified"><span class="status-dot"></span>Full Consistency (0 issues)</span>`
                    : `<span class="quality-badge status-flagged"><span class="status-dot"></span>${totalIssues} Flags to Review</span>`;

                cardContent = `
                    <div class="quality-stat-tiles">
                        <div class="stat-tile">
                            <span class="stat-number">${inTextCount}</span>
                            <span class="stat-label">In-Text Citations</span>
                        </div>
                        <div class="stat-tile">
                            <span class="stat-number">${refsCount}</span>
                            <span class="stat-label">Bibliography Refs</span>
                        </div>
                        <div class="stat-tile ${unmatched > 0 ? "stat-tile-flag" : ""}">
                            <span class="stat-number">${unmatched}</span>
                            <span class="stat-label">Unmatched Citations</span>
                        </div>
                        <div class="stat-tile">
                            <span class="stat-number">${uncited}</span>
                            <span class="stat-label">Uncited References</span>
                        </div>
                        <div class="stat-tile">
                            <span class="stat-number">${dups}</span>
                            <span class="stat-label">Duplicate Numbers</span>
                        </div>
                    </div>

                    ${totalIssues === 0 ? `
                        <div class="quality-success-banner">
                            <div class="banner-icon-col">
                                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
                            </div>
                            <div class="banner-text-col">
                                <strong>Full In-Text Citation Alignment</strong>
                                <p>All ${inTextCount} detected in-text citations ([1] through [${inTextCount}]) correspond directly to detected bibliography entries with zero duplicate numbers.</p>
                            </div>
                        </div>
                    ` : ""}

                    ${citationFindings.length ? `
                        <div class="quality-findings-list">
                            ${citationFindings.map(finding => `
                                <div class="quality-finding-row">
                                    <span class="severity-badge severity-${esc(finding.severity || "low")}">${esc(finding.severity || "low")} severity</span>
                                    <span class="human-review-badge">Human Review Required</span>
                                    <p class="finding-message">${esc(finding.message || finding.type || "Citation issue")}</p>
                                </div>
                            `).join("")}
                        </div>
                    ` : ""}

                    ${Array.isArray(report.references) && report.references.length > 0 ? `
                        <details class="quality-expandable-drawer">
                            <summary>View parsed bibliography references (${report.references.length})</summary>
                            <div class="drawer-content">
                                <ol class="parsed-bibliography-list">
                                    ${report.references.slice(0, 15).map(ref => `
                                        <li><strong>[${ref.number}]</strong> ${esc(ref.text)}</li>
                                    `).join("")}
                                    ${report.references.length > 15 ? `<p class="more-refs-note">... and ${report.references.length - 15} additional parsed references.</p>` : ""}
                                </ol>
                            </div>
                        </details>
                    ` : ""}
                `;

                return `
                    <article class="quality-card">
                        <div class="quality-card-header">
                            <div>
                                <strong class="quality-card-title">${label}</strong>
                                <p class="quality-card-subtitle">NeurIPS-format numbered bracket citation analysis</p>
                            </div>
                            ${statusBadge}
                        </div>
                        <div class="quality-card-body">
                            ${cardContent}
                        </div>
                    </article>
                `;
            } else if (key === "document_structure") {
                const figureCount = Number(report.figure_count || 0);
                const tableCount = Number(report.table_caption_count || 0);
                const tableLinesCount = Number(report.markdown_table_line_count || 0);
                const equationCount = Number(report.equation_like_line_count || 0);
                const tableDups = report.table_numbering?.duplicate_numbers || [];
                const figureGaps = (report.figure_numbering?.numbering_gaps || []).length;
                const tableGaps = (report.table_numbering?.numbering_gaps || []).length;

                const hasNotices = tableDups.length > 0 || figureGaps > 0 || tableGaps > 0;
                const statusBadge = !hasNotices
                    ? `<span class="quality-badge status-verified"><span class="status-dot"></span>Structure Mapped</span>`
                    : `<span class="quality-badge status-notice"><span class="status-dot"></span>Numbering Notice</span>`;

                cardContent = `
                    <div class="quality-stat-tiles">
                        <div class="stat-tile">
                            <span class="stat-number">${figureCount}</span>
                            <span class="stat-label">Figures Detected</span>
                        </div>
                        <div class="stat-tile">
                            <span class="stat-number">${tableCount}</span>
                            <span class="stat-label">Table Captions</span>
                        </div>
                        <div class="stat-tile">
                            <span class="stat-number">${tableLinesCount}</span>
                            <span class="stat-label">Table Data Lines</span>
                        </div>
                        <div class="stat-tile">
                            <span class="stat-number">${equationCount}</span>
                            <span class="stat-label">Equation-like Lines</span>
                        </div>
                    </div>

                    ${tableDups.length > 0 ? `
                        <div class="quality-notice-banner">
                            <div class="notice-icon-col">
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
                            </div>
                            <div class="notice-text-col">
                                <strong>Table Numbering Observation:</strong>
                                <p>Table caption number ${tableDups.join(", ")} appears multiple times in extracted text. This commonly occurs in complex manuscripts with sub-tables or multi-paragraph descriptions. Review manuscript layout.</p>
                            </div>
                        </div>
                    ` : ""}

                    ${Array.isArray(report.tables) && report.tables.length > 0 ? `
                        <details class="quality-expandable-drawer">
                            <summary>View detected table captions (${report.tables.length})</summary>
                            <div class="drawer-content">
                                <ul class="detected-captions-list">
                                    ${report.tables.map(t => `
                                        <li><strong>Table ${t.number}:</strong> ${esc(t.caption)} <span class="caption-line-tag">line ${t.line}</span></li>
                                    `).join("")}
                                </ul>
                            </div>
                        </details>
                    ` : ""}
                `;

                return `
                    <article class="quality-card">
                        <div class="quality-card-header">
                            <div>
                                <strong class="quality-card-title">${label}</strong>
                                <p class="quality-card-subtitle">Extracted text element counts and caption sequencing</p>
                            </div>
                            ${statusBadge}
                        </div>
                        <div class="quality-card-body">
                            ${cardContent}
                        </div>
                    </article>
                `;
            } else {
                const claimsChecked = Number(report.numeric_claims_checked || 0);
                const findings = Array.isArray(report.findings) ? report.findings : [];

                const statusBadge = findings.length === 0
                    ? `<span class="quality-badge status-verified"><span class="status-dot"></span>0 Inconsistencies</span>`
                    : `<span class="quality-badge status-flagged"><span class="status-dot"></span>${findings.length} Heuristic Flags</span>`;

                cardContent = `
                    <div class="consistency-heuristic-callout">
                        <div class="callout-lead">
                            <span class="heuristic-pill">HEURISTIC CHECK</span>
                            <strong>Cross-Section Quantitative Verification</strong>
                        </div>
                        <p>${claimsChecked} numeric claims cross-referenced across checked academic sections. Numerical differences often reflect legitimate distinctions between experimental conditions (e.g. base model vs. big model training times). These flags are exploratory alerts requiring reviewer judgment.</p>
                    </div>

                    ${findings.length ? `
                        <div class="consistency-findings-grid">
                            ${findings.map((finding, idx) => `
                                <div class="consistency-finding-card">
                                    <div class="finding-card-top">
                                        <div class="comparison-pair">
                                            <span class="section-tag">${esc(finding.sections?.[0] || "")}</span>
                                            <span class="comparison-arrow">&harr;</span>
                                            <span class="section-tag">${esc(finding.sections?.[1] || "")}</span>
                                        </div>
                                        <div class="values-display">
                                            <span class="values-label">Values:</span>
                                            <strong class="values-strong">${esc((finding.values || []).join(" vs "))} ${esc(finding.unit || "")}</strong>
                                        </div>
                                        <span class="severity-badge severity-${esc(finding.severity || "medium")}">
                                            ${esc(finding.severity || "medium")} severity &middot; Human Review Required
                                        </span>
                                    </div>

                                    <div class="shared-terms-strip">
                                        <span class="terms-label">Shared Context:</span>
                                        ${(finding.shared_context_terms || []).map(t => `<code class="context-tag">${esc(t)}</code>`).join(" ")}
                                    </div>

                                    <div class="evidence-excerpts-box">
                                        ${(finding.evidence || []).map((text, eIdx) => `
                                            <div class="excerpt-item">
                                                <span class="excerpt-source">Excerpt from ${esc(finding.sections?.[eIdx] || `Section ${eIdx + 1}`)}:</span>
                                                <blockquote class="excerpt-quote">${esc(text)}</blockquote>
                                            </div>
                                        `).join("")}
                                    </div>
                                </div>
                            `).join("")}
                        </div>
                    ` : `
                        <div class="quality-success-banner">
                            <div class="banner-icon-col">
                                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
                            </div>
                            <div class="banner-text-col">
                                <strong>No Numeric Discrepancies Flagged</strong>
                                <p>${claimsChecked} numeric claims were cross-referenced with zero conflicting shared-context values detected.</p>
                            </div>
                        </div>
                    `}
                `;

                return `
                    <article class="quality-card">
                        <div class="quality-card-header">
                            <div>
                                <strong class="quality-card-title">${label}</strong>
                                <p class="quality-card-subtitle">Automated comparison of numeric values sharing contextual terms</p>
                            </div>
                            ${statusBadge}
                        </div>
                        <div class="quality-card-body">
                            ${cardContent}
                        </div>
                    </article>
                `;
            }
        });

        list.innerHTML = cards.join("");
    } catch (error) {
        list.innerHTML = '<p class="placeholder-text">Supplemental quality analysis could not be loaded.</p>';
        summary.textContent = "Load failed";
        console.error("Quality analysis loading error:", error);
    }
}

async function loadEvidenceForReview(reviewFilename) {
    const evidenceList = document.getElementById("evidence-list");
    const evidenceSummary = document.getElementById("evidence-summary");
    if (!evidenceList || !evidenceSummary) return;

    try {
        let records = [];

    // Try review-specific evidence lookup endpoint first
    try {
        const response = await fetch(
            `/api/evidence-for-review/${encodeURIComponent(currentRunDir)}/${encodeURIComponent(reviewFilename)}`
        );
        if (response.ok) {
            const data = await response.json();
            if (Array.isArray(data.evidence) && data.evidence.length > 0) {
                records = data.evidence;
            }
        }
    } catch (_) {}

    // Fallback: search by candidate paper filename
    if (!records.length) {
        const paperStem = reviewFilename.replace(/_(?:custom_openai|openai|anthropic|ollama|azure|bedrock).*$/, "");
        for (const candidate of [`${paperStem}.pdf`, `${paperStem}.docx`, paperStem]) {
            try {
                const response = await fetch(
                    `/api/evidence/${encodeURIComponent(currentRunDir)}/${encodeURIComponent(candidate)}`
                );
                if (response.ok) {
                    const data = await response.json();
                    if (Array.isArray(data.evidence) && data.evidence.length > 0) {
                        records = data.evidence;
                        break;
                    }
                }
            } catch (_) {}
        }
    }

    if (!records.length) {
        evidenceList.innerHTML = '<p class="placeholder-text">No saved evidence is available for this review yet. Evidence records are generated on the next review run.</p>';
        evidenceSummary.textContent = "No evidence saved";
        return;
    }

    const verifiedCount = records.filter(item => {
        const hasQ = Boolean(item.quote && item.quote.trim() && item.quote.trim().toUpperCase() !== "N/A" && item.quote.trim() !== "No quote supplied");
        return hasQ && String(item.verification_status || "").toLowerCase() === "verified";
    }).length;
    const unverifiedCount = records.length - verifiedCount;
    evidenceSummary.textContent = `${verifiedCount} Verified · ${unverifiedCount} Unverified · ${records.length} Total quotes`;

    // Track per-criterion quote index to generate canonical IDs matching report citations (e.g. data_quality:Q1)
    const criterionQuoteCounts = {};

    evidenceList.innerHTML = records.map((item, index) => {
        const criterionId = item.criterion_id || "unspecified";
        criterionQuoteCounts[criterionId] = (criterionQuoteCounts[criterionId] || 0) + 1;
        const canonicalId = `${criterionId}:Q${criterionQuoteCounts[criterionId]}`;

        const hasValidQuote = Boolean(item.quote && item.quote.trim() && item.quote.trim().toUpperCase() !== "N/A" && item.quote.trim() !== "No quote supplied");
        const verified = hasValidQuote && String(item.verification_status || "").toLowerCase() === "verified";
        const statusClass = verified ? "evidence-status-verified" : "evidence-status-unverified";
        const statusLabel = verified ? "Quote verified in source" : (hasValidQuote ? "Unverified quotation" : "Unverified quotation (No verbatim quote)");

        // Truthful page reference without fabrication
        let pageText = "Page unrecorded";
        if (Array.isArray(item.pdf_page_numbers) && item.pdf_page_numbers.length > 0) {
            pageText = `PDF Page ${item.pdf_page_numbers.join(", ")}`;
        } else if (item.page_reference && item.page_reference !== "N/A") {
            pageText = item.page_reference;
        } else if (item.is_docx || (item.source_file && item.source_file.toLowerCase().endsWith(".docx")) || (activeReviewFilename && activeReviewFilename.toLowerCase().includes(".docx"))) {
            pageText = "DOCX (Unpaged format)";
        }

        // Academic section and mapping info
        const sectionText = item.academic_section
            ? item.academic_section.replace(/_/g, " ")
            : "Unmapped section";
        const mappingStatus = item.section_mapping_status
            ? item.section_mapping_status.replace(/_/g, " ")
            : null;
        const mappingMethod = item.section_mapping_method
            ? item.section_mapping_method.replace(/_/g, " ")
            : null;

        return `
            <article class="evidence-card ${verified ? "card-verified" : "card-unverified"}" id="evidence-card-${escapeHtml(canonicalId)}" data-status="${verified ? "verified" : "unverified"}" data-criterion="${escapeHtml(criterionId)}" data-canonical-id="${escapeHtml(canonicalId)}">
                <div class="evidence-card-header">
                    <div class="evidence-identifiers">
                        <span class="evidence-order-badge">#${index + 1}</span>
                        <code class="evidence-canonical-id" title="Canonical evidence citation identifier">[Evidence: ${escapeHtml(canonicalId)}]</code>
                        <span class="evidence-criterion-chip">${escapeHtml(criterionId.replace(/_/g, " "))}</span>
                    </div>
                    <div class="evidence-status-wrapper">
                        <span class="evidence-status-pill ${statusClass}">
                                <span class="status-dot"></span>
                                ${statusLabel}
                            </span>
                        </div>
                    </div>

                    <div class="evidence-quote-box">
                        <div class="evidence-quote-header">
                            <span class="evidence-quote-type">${hasValidQuote ? "VERBATIM SOURCE EXCERPT" : "NO QUOTE EXTRACTED"}</span>
                            <span class="evidence-page-ref">${escapeHtml(pageText)}</span>
                        </div>
                        <blockquote class="evidence-quote">${escapeHtml(hasValidQuote ? item.quote : "No verbatim excerpt was extracted for this criterion assertion.")}</blockquote>
                    </div>

                    <div class="evidence-meta-strip">
                        <div class="meta-tag">
                            <span class="meta-label">Academic Section:</span>
                            <span class="meta-val section-val">${escapeHtml(sectionText)}</span>
                        </div>
                        ${mappingStatus ? `
                        <div class="meta-tag">
                            <span class="meta-label">Mapping:</span>
                            <span class="meta-val">${escapeHtml(mappingStatus)}${mappingMethod ? ` (${escapeHtml(mappingMethod)})` : ""}</span>
                        </div>` : ""}
                    </div>

                    ${item.verification_note ? `
                    <div class="evidence-verification-note">
                        <div class="note-heading">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
                            <span>Verification Notice</span>
                        </div>
                        <p class="note-text">${escapeHtml(item.verification_note)}</p>
                    </div>` : ""}

                    <div class="evidence-semantic-claim">
                        <div class="semantic-claim-header">
                            <span class="semantic-claim-title">Extractor Rubric Alignment Claim</span>
                            <span class="semantic-claim-warning-tag">AI Extraction Claim &middot; Does Not Prove Relevance</span>
                        </div>
                        <p class="semantic-claim-body">${escapeHtml(item.relevance || "No relevance statement provided.")}</p>
                        <p class="semantic-disclaimer-note">Notice (Invariant 5): Exact textual match in the paper does not prove semantic rubric alignment. Alignment remains an AI assertion requiring peer review.</p>
                    </div>

                    <details class="evidence-justification">
                        <summary class="evidence-justification-summary">
                            <span>Extractor Score &amp; Rubric Justification</span>
                            <span class="justification-score-val">${escapeHtml(String(item.score ?? "—"))} / 5</span>
                        </summary>
                        <div class="evidence-justification-body">
                            <p>${escapeHtml(item.score_justification || "No justification recorded.")}</p>
                        </div>
                    </details>
                </article>
            `;
        }).join("");

        // Wire filter buttons
        const filterBar = document.getElementById("evidence-filter-bar");
        if (filterBar) {
            filterBar.querySelectorAll(".evidence-filter-btn").forEach(btn => {
                btn.onclick = () => {
                    filterBar.querySelectorAll(".evidence-filter-btn").forEach(b => b.classList.remove("active"));
                    btn.classList.add("active");
                    const filter = btn.dataset.filter;
                    const cards = evidenceList.querySelectorAll(".evidence-card");
                    cards.forEach(card => {
                        if (filter === "all") {
                            card.hidden = false;
                        } else if (filter === "verified") {
                            card.hidden = card.dataset.status !== "verified";
                        } else if (filter === "unverified") {
                            card.hidden = card.dataset.status !== "unverified";
                        }
                    });
                };
            });
        }
    } catch (error) {
        evidenceList.innerHTML = '<p class="placeholder-text">Evidence could not be loaded. Please check the server logs.</p>';
        evidenceSummary.textContent = "Load failed";
        console.error("Evidence loading error:", error);
    }
}

function getRecClass(rec) {
    if (!rec) return "";
    const l = rec.toLowerCase();
    if (l.includes("accept") && !l.includes("revision")) return "rec-accept";
    if (l.includes("accept with revision") || l.includes("minor")) return "rec-revision";
    if (l.includes("revise") || l.includes("resubmit") || l.includes("major")) return "rec-resubmit";
    if (l.includes("reject")) return "rec-reject";
    return "";
}

// ---- Stage Indicators ----

const stageMap = {
    "Ingestion": "stage-ingest",
    "Librarian": "stage-ingest",
    "Extraction": "stage-extraction",
    "Fact-Check": "stage-extraction",
    "Synthesis": "stage-synthesis",
    "Output": "stage-output",
    "Judge-Compare": "stage-extraction",
    "Judge-Adjudicate": "stage-synthesis",
};

function activateStage(name) {
    const id = stageMap[name];
    if (id) {
        const el = document.getElementById(id);
        el?.classList.add("active");
        el?.classList.remove("completed");
    }
}

function completeStage(name) {
    const id = stageMap[name];
    if (id) {
        const el = document.getElementById(id);
        el?.classList.remove("active");
        el?.classList.add("completed");
    }
}

function clearStages() {
    document.querySelectorAll(".stage-item").forEach(el => {
        el.classList.remove("active", "completed");
    });
}

// ---- Log ----

function addLog(message, level = "info") {
    const entry = document.createElement("div");
    entry.className = `log-entry log-${level}`;
    const now = new Date();
    const time = now.toLocaleTimeString("en-US", { hour12: false });
    entry.innerHTML = `<span class="log-time">${time}</span><span class="log-msg">${escapeHtml(message)}</span>`;
    logContent.appendChild(entry);
    logContent.scrollTop = logContent.scrollHeight;
}

// ---- Helpers ----

function startElapsedTimer() {
    if (elapsedInterval) clearInterval(elapsedInterval);
    elapsedInterval = setInterval(() => {
        if (runStartTime) {
            const s = (Date.now() - runStartTime) / 1000;
            elapsedDisplay.textContent = formatDuration(s);
        }
    }, 1000);
}

function formatDuration(seconds) {
    if (seconds < 60) return `${seconds.toFixed(0)}s`;
    if (seconds < 3600) return `${(seconds / 60).toFixed(1)}m`;
    return `${(seconds / 3600).toFixed(1)}h`;
}

function escapeHtml(str) {
    if (!str) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

const CRITERION_FRIENDLY_NAMES = {
    "originality_novelty": "Originality",
    "theoretical_contribution": "Theoretical",
    "empirical_rigor": "Empirical",
    "data_quality": "Data Quality",
    "identification_strategy": "Identification",
    "statistical_robustness": "Statistics",
    "presentation_clarity": "Presentation",
    "policy_relevance": "Policy",
};

window.jumpToEvidenceCitation = function(canonicalId) {
    const cleanCit = (canonicalId || "").trim();
    if (!cleanCit) return;

    const reviewModal = document.getElementById("review-modal");
    if (reviewModal && reviewModal.style.display !== "none") {
        if (typeof switchModalSection === "function") {
            switchModalSection("evidence-panel");
        }
    } else {
        const viewEvBtn = document.getElementById("dashboard-view-evidence-btn");
        if (viewEvBtn) {
            viewEvBtn.click();
        }
    }

    setTimeout(() => {
        const card = document.getElementById(`evidence-card-${cleanCit}`)
            || Array.from(document.querySelectorAll("#evidence-list .evidence-card, .evidence-card")).find(c =>
                c.textContent.includes(cleanCit) || (c.dataset && c.dataset.canonicalId === cleanCit)
            );
        if (card) {
            card.scrollIntoView({ behavior: "smooth", block: "center" });
            card.classList.remove("section-target-highlight");
            void card.offsetWidth;
            card.classList.add("section-target-highlight");
        }
    }, 150);
};

function formatEvidenceCitations(html) {
    if (!html || typeof html !== "string") return "";

    // 1. Clean inline ✓Evidence tokens
    html = html.replace(/✓\s*Evidence\b/gi, '<span class="inline-verified-tag" title="Verified source evidence"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg> Verified</span>');

    // 2. Clean internal pipeline status markers
    html = html.replace(/\*?\(Status:\s*filtered_by_evidence_gate\)\*?/gi, '<span class="status-advisory-tag filter-advisory"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg> Withheld: Lacks Source Evidence</span>');
    html = html.replace(/\*?\(Status:\s*verified_content\)\*?/gi, '');

    // 3. Convert [Evidence: criterion:Q#] to human-readable citation badges
    return html.replace(/\[(?:(Verified|Unverified)\s+)?Evidence:\s*([^\]]+)\]/gi, (match, status, content) => {
        const cleanContent = content.trim();
        const isUnverified = (status && status.toLowerCase() === "unverified") || cleanContent.toLowerCase().includes("unverified");
        const badgeClass = isUnverified ? "cite-unverified" : "cite-verified";
        const iconSvg = isUnverified
            ? `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`
            : `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>`;

        let displayLabel = cleanContent;
        const parts = cleanContent.split(":");
        if (parts.length === 2) {
            const critKey = parts[0].toLowerCase();
            const qNum = parts[1].toUpperCase();
            const friendly = CRITERION_FRIENDLY_NAMES[critKey] || parts[0].replace(/_/g, " ");
            displayLabel = `${friendly} [${qNum}]`;
        } else if (/^Q\d+$/i.test(cleanContent)) {
            displayLabel = `Evidence [${cleanContent.toUpperCase()}]`;
        }

        return `<span class="evidence-cite-badge ${badgeClass}" title="Grounding citation: ${cleanContent} — Click to view in Grounding Matrix" data-citation="${cleanContent}" onclick="jumpToEvidenceCitation('${cleanContent}')">${iconSvg}<span>${escapeHtml(displayLabel)}</span></span>`;
    });
}

function renderSimpleMarkdown(text) {
    if (!text) return "";
    let html = escapeHtml(text);
    html = formatEvidenceCitations(html);
    html = html.replace(/^### (.+)$/gm, "<h4>$1</h4>");
    html = html.replace(/^## (.+)$/gm, "<h3>$1</h3>");
    html = html.replace(/^# (.+)$/gm, "<h2>$1</h2>");
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");
    html = html.replace(/^- (.+)$/gm, "<li>$1</li>");
    html = html.replace(/^(\d+)\. (.+)$/gm, "<li>$2</li>");
    html = html.replace(/\n\n/g, "</p><p>");
    html = "<p>" + html + "</p>";
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    html = html.replace(/^---$/gm, "<hr>");
    return html;
}

function renderPublicationReport(text) {
    if (!text) return '<p class="placeholder-text">Empty review report.</p>';

    // Clean markdown bolding (**) for robust regex matching
    const cleanText = (text || "").replace(/\*\*/g, "");

    // 1. Title
    const titleMatch = cleanText.match(/^#\s+(?:Review:\s*)?([^\r\n]+)/m);
    let paperTitle = titleMatch ? titleMatch[1].trim() : "";
    const INVALID_TITLES = ["keywords", "keyword", "abstract", "untitled", "no title found", "none", "title not identified"];
    if (!paperTitle || INVALID_TITLES.includes(paperTitle.toLowerCase()) || paperTitle.toLowerCase().startsWith("keyword")) {
        const activeRun = (typeof allRunsList !== "undefined" && Array.isArray(allRunsList)) ? allRunsList.find(r => r.name === currentRunDir) : null;
        if (activeRun && activeRun.paper_title && !INVALID_TITLES.includes(activeRun.paper_title.toLowerCase()) && !activeRun.paper_title.toLowerCase().startsWith("keyword")) {
            paperTitle = activeRun.paper_title;
        } else if (activeRun && activeRun.original_filename) {
            paperTitle = activeRun.original_filename.replace(/\.[^/.]+$/, "").replace(/_/g, " ");
        } else if (typeof activeReviewFilename !== "undefined" && activeReviewFilename) {
            paperTitle = activeReviewFilename.split("_ollama")[0].replace(/_/g, " ");
        } else {
            paperTitle = "Research Manuscript Review";
        }
    }

    // 2. Score
    const scoreMatch = cleanText.match(/(?:Overall Score:\s*|Score:\s*)([\d.]+)\s*\/\s*100/i);
    const overallScore = scoreMatch ? parseFloat(scoreMatch[1]) : null;

    // 3. Recommendation
    const recMatch = cleanText.match(/(?:Final System Recommendation:\s*|Recommendation:\s*)([^\r\n]+)/i);
    const recommendation = recMatch ? recMatch[1].trim() : "Pending";

    // 4. Provisional Recommendation
    const provMatch = cleanText.match(/(?:Provisional Recommendation:\s*)([^\r\n]+)/i);
    const provisional = provMatch ? provMatch[1].trim() : null;

    // 5. Evidence & Criteria Counts
    const verifiedMatch = cleanText.match(/Verified Evidence:\s*(\d+)/i)
        || cleanText.match(/Verified Quotations?:\s*(\d+)/i)
        || cleanText.match(/Verified Quotes?:\s*(\d+)/i);
    const verifiedCount = verifiedMatch ? parseInt(verifiedMatch[1], 10) : 0;

    const unverifiedMatch = cleanText.match(/Unverified Evidence:\s*(\d+)/i)
        || cleanText.match(/Unverified Quotations?:\s*(\d+)/i)
        || cleanText.match(/Unverified Quotes?:\s*(\d+)/i);
    const unverifiedCount = unverifiedMatch ? parseInt(unverifiedMatch[1], 10) : 0;

    const criteriaMatch = cleanText.match(/Criteria:\s*(\d+)\s*\/\s*(\d+)/i);
    const criteriaCount = criteriaMatch ? `${criteriaMatch[1]}/${criteriaMatch[2]}` : "8/8";

    const totalQuotes = verifiedCount + unverifiedCount;
    const coveragePercent = totalQuotes > 0 ? Math.round((verifiedCount / totalQuotes) * 100) : 0;

    // Helper: section extractor
    function extractSection(headingRegex, nextHeadingRegex) {
        const start = text.search(headingRegex);
        if (start === -1) return "";
        const sub = text.substring(start);
        const match = sub.match(headingRegex);
        if (!match) return "";
        const bodyStart = match[0].length;
        const rest = sub.substring(bodyStart);
        const nextMatch = rest.search(nextHeadingRegex || /\n##\s+/);
        return (nextMatch !== -1 ? rest.substring(0, nextMatch) : rest).trim();
    }

    const execSummary = extractSection(/\n##\s+Executive Summary/i);
    const warnings = extractSection(/\n###?\s+Warnings/i, /\n##\s+/);
    const majorStrengths = extractSection(/\n###\s+Major Strengths/i, /\n###\s+/);
    const majorConcerns = extractSection(/\n###\s+Major Concerns/i, /\n###\s+/);
    const minorIssues = extractSection(/\n###\s+Minor Issues/i, /\n##\s+/);
    const revisionSuggestions = extractSection(/\n##\s+Revision Suggestions/i, /\n##\s+/);

    // Score radial dial calculations
    const scoreVal = overallScore !== null ? overallScore : 0;
    const circumference = 2 * Math.PI * 42; // ~263.89
    const offset = circumference - (scoreVal / 100) * circumference;

    let scoreColor = "var(--brand-primary)";
    if (scoreVal >= 80) scoreColor = "var(--status-success)";
    else if (scoreVal >= 65) scoreColor = "var(--brand-secondary)";
    else if (scoreVal >= 50) scoreColor = "var(--status-warning)";
    else scoreColor = "var(--status-error)";

    let html = `
    <div class="publication-report">
        <!-- Hero Header -->
        <div class="report-hero-card">
            <div class="report-hero-left">
                <span class="overview-eyebrow">PUBLICATION AUDIT REPORT</span>
                <h2>${escapeHtml(paperTitle)}</h2>
                <div class="report-hero-meta">
                    <span class="badge-status ${getStatusBadgeClass(recommendation)}">${escapeHtml(recommendation)}</span>
                    ${provisional ? `<span class="badge-rec">Provisional: ${escapeHtml(provisional)}</span>` : ''}
                    <span class="badge-score">Criteria Evaluated: ${criteriaCount}</span>
                    <span class="badge-score">Verified Evidence: ${totalQuotes > 0 ? `${verifiedCount} / ${totalQuotes} (${coveragePercent}%)` : `${verifiedCount} verified`}</span>
                </div>
            </div>
            <div class="report-hero-right">
                <div class="score-radial-container" style="margin: 0;">
                    <svg class="score-radial-svg" viewBox="0 0 100 100" aria-hidden="true">
                        <circle class="score-radial-bg" cx="50" cy="50" r="42" />
                        <circle class="score-radial-fill" cx="50" cy="50" r="42" 
                                stroke="${scoreColor}" 
                                stroke-dasharray="${circumference.toFixed(2)}" 
                                stroke-dashoffset="${offset.toFixed(2)}" />
                    </svg>
                    <div class="metric-value-wrap score-radial-center">
                        <strong class="review-metric-value">${overallScore !== null ? overallScore.toFixed(1) : '—'}</strong>
                    </div>
                </div>
            </div>
        </div>
    `;

    // Warnings Banner
    if (warnings) {
        html += `
        <div class="evidence-grounding-disclaimer" style="border-left-color: var(--status-warning); background: var(--status-warning-bg); margin-bottom: 1.5rem;">
            <div class="disclaimer-icon" style="color: var(--status-warning); font-size: 1.2rem;">⚠️</div>
            <div class="disclaimer-text">
                <strong style="color: var(--status-warning); font-size: 0.88rem;">Evaluation Warnings &amp; Governance Flags</strong>
                <div style="margin-top: 0.35rem; font-size: 0.82rem; line-height: 1.5;">${renderSimpleMarkdown(warnings)}</div>
            </div>
        </div>`;
    }

    // Executive Summary
    if (execSummary) {
        html += `
        <div class="report-section-card">
            <h3><span style="color: var(--brand-primary);">◈</span> Executive Summary</h3>
            <p style="font-size: 0.94rem; line-height: 1.7; color: var(--text-secondary);">${formatEvidenceCitations(escapeHtml(execSummary))}</p>
        </div>`;
    }

    // Strengths & Concerns Comparison
    if (majorStrengths || majorConcerns || minorIssues) {
        html += `
        <div class="report-section-card">
            <h3><span style="color: var(--brand-primary);">❖</span> Peer Review Critique</h3>
            <div class="strengths-weaknesses-cols">
                <div class="col-card strengths-col">
                    <h4 style="color: var(--status-success);">✓ Major Strengths</h4>
                    <div>${renderSimpleMarkdown(majorStrengths || "No major strengths noted.")}</div>
                </div>
                <div class="col-card weaknesses-col">
                    <h4 style="color: var(--status-warning);">! Concerns &amp; Weaknesses</h4>
                    <div>${renderSimpleMarkdown(majorConcerns || "No major concerns noted.")}</div>
                    ${minorIssues ? `<div style="margin-top: 1rem; border-top: 1px solid var(--border-subtle); padding-top: 0.75rem;"><strong style="font-size: 0.8rem; color: var(--text-muted);">Minor Issues:</strong>${renderSimpleMarkdown(minorIssues)}</div>` : ''}
                </div>
            </div>
        </div>`;
    }

    // Revision Suggestions
    if (revisionSuggestions) {
        html += `
        <div class="report-section-card">
            <h3><span style="color: var(--brand-primary);">✦</span> Actionable Revision Suggestions</h3>
            <div style="font-size: 0.9rem; line-height: 1.6; color: var(--text-secondary);">${renderSimpleMarkdown(revisionSuggestions)}</div>
        </div>`;
    }

    html += `</div>`;
    return html;
}

function renderMarkdown(text) {
    if (!text) return "";
    // If text looks like a synthesized review report, use publication renderer
    if (text.includes("## Overall Assessment") || text.includes("Overall Score:") || text.startsWith("# Review:")) {
        return renderPublicationReport(text);
    }
    return renderSimpleMarkdown(text);
}

// ---- Sidebar & Configuration Drawer State Management ----

function openConfigPanel() {
    const panel = document.getElementById("config-panel");
    const backdrop = document.getElementById("sidebar-backdrop");
    const headerBtn = document.getElementById("header-config-toggle");
    const sidebarBtn = document.getElementById("sidebar-config-btn");
    const toggleBtn = document.getElementById("panel-toggle");
    if (!panel) return;
    panel.classList.add("open");
    panel.classList.remove("collapsed");
    if (backdrop) backdrop.classList.add("active");
    if (window.innerWidth <= 900) {
        document.body.classList.add("modal-open");
    }
    if (headerBtn) {
        headerBtn.setAttribute("aria-expanded", "true");
        headerBtn.classList.add("active");
    }
    if (sidebarBtn) {
        sidebarBtn.setAttribute("aria-expanded", "true");
        sidebarBtn.classList.add("active");
    }
    if (toggleBtn) {
        toggleBtn.title = "Collapse configuration panel";
    }
}

function closeConfigPanel() {
    const panel = document.getElementById("config-panel");
    const backdrop = document.getElementById("sidebar-backdrop");
    const headerBtn = document.getElementById("header-config-toggle");
    const sidebarBtn = document.getElementById("sidebar-config-btn");
    if (!panel) return;
    panel.classList.remove("open");
    panel.classList.add("collapsed");
    if (backdrop) backdrop.classList.remove("active");
    document.body.classList.remove("modal-open");
    if (headerBtn) {
        headerBtn.setAttribute("aria-expanded", "false");
        headerBtn.classList.remove("active");
    }
    if (sidebarBtn) {
        sidebarBtn.setAttribute("aria-expanded", "false");
        sidebarBtn.classList.remove("active");
    }
}

function toggleConfigPanel() {
    const panel = document.getElementById("config-panel");
    if (!panel) return;
    if (panel.classList.contains("open")) {
        closeConfigPanel();
    } else {
        openConfigPanel();
    }
}

function openMobileConfigPanel() {
    openConfigPanel();
}

function closeMobileConfigPanel() {
    closeConfigPanel();
}

// ---- Mobile Header Actions Dropdown Menu (<640px) ----

function openMobileActionsMenu() {
    const controls = document.getElementById("header-run-controls");
    const btn = document.getElementById("mobile-actions-toggle");
    if (!controls) return;
    controls.classList.add("open");
    if (btn) {
        btn.setAttribute("aria-expanded", "true");
        btn.classList.add("active");
    }
}

function closeMobileActionsMenu() {
    const controls = document.getElementById("header-run-controls");
    const btn = document.getElementById("mobile-actions-toggle");
    if (!controls) return;
    controls.classList.remove("open");
    if (btn) {
        btn.setAttribute("aria-expanded", "false");
        btn.classList.remove("active");
    }
}

function toggleMobileActionsMenu() {
    const controls = document.getElementById("header-run-controls");
    if (!controls) return;
    if (controls.classList.contains("open")) {
        closeMobileActionsMenu();
    } else {
        openMobileActionsMenu();
    }
}

// ---- Ollama Real Health Check & Diagnostics ----

let isCheckingOllama = false;

async function checkOllamaHealth() {
    if (isCheckingOllama) return;
    isCheckingOllama = true;

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 4000);

    try {
        const res = await fetch("/api/health", { signal: controller.signal });
        clearTimeout(timeoutId);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        updateOllamaHealthUI(data);
    } catch (err) {
        clearTimeout(timeoutId);
        updateOllamaHealthUI({
            status: err.name === "AbortError" ? "timeout" : "offline",
            model: "llama3.2:3b",
            detail: err.name === "AbortError" ? "Health check timed out" : "Local Ollama service unreachable"
        });
    } finally {
        isCheckingOllama = false;
    }
}

function updateOllamaHealthUI(health) {
    const pill = document.getElementById("ollama-status-pill");
    const dot = document.getElementById("ollama-status-dot");
    const text = document.getElementById("ollama-status-text");
    if (!pill || !dot || !text) return;

    pill.classList.remove("online", "offline", "warning", "checking");
    dot.classList.remove("online", "offline", "warning", "checking");

    if (health.status === "online") {
        pill.classList.add("online");
        dot.classList.add("online");
        text.textContent = `Ollama Online · ${health.model || "llama3.2:3b"}`;
        pill.title = health.detail || `Local Ollama service active with ${health.model}`;
    } else if (health.status === "model_missing") {
        pill.classList.add("warning");
        dot.classList.add("warning");
        text.textContent = "Model Not Found";
        pill.title = health.detail || `Required model ${health.model} is not downloaded in Ollama`;
    } else if (health.status === "timeout") {
        pill.classList.add("warning");
        dot.classList.add("warning");
        text.textContent = "Ollama Timeout";
        pill.title = "Health check request to local Ollama service timed out";
    } else {
        pill.classList.add("offline");
        dot.classList.add("offline");
        text.textContent = "Ollama Offline";
        pill.title = "Local inference service is unavailable (localhost:11434). Run 'ollama serve' to start.";
    }
}

let ollamaHealthInterval = null;

function initOllamaHealthCheck() {
    checkOllamaHealth();
    if (ollamaHealthInterval) clearInterval(ollamaHealthInterval);
    ollamaHealthInterval = setInterval(checkOllamaHealth, 30000);

    // Refresh immediately when tab gains focus/visibility
    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") {
            checkOllamaHealth();
        }
    });
}

// ---- Sidebar Tab Switching ----

function switchSidebarTab(tabName) {
    if (tabName !== "config" && Object.keys(configDirty).length > 0) {
        if (!confirm("You have unsaved configuration changes. Discard them and switch tabs?")) {
            return;
        }
        discardConfigEdits();
    }
    document.querySelectorAll(".sidebar-tab").forEach(t => t.classList.remove("active"));
    const tabEl = document.querySelector(`.sidebar-tab[data-tab="${tabName}"]`);
    if (tabEl) tabEl.classList.add("active");
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
    const panelEl = document.getElementById(`tab-${tabName}`);
    if (panelEl) panelEl.classList.add("active");
}

function discardConfigEdits() {
    configDirty = {};
    renderConfigEditor(originalConfig);
    configSaveBtn.style.display = "none";
    const banner = document.getElementById("config-unsaved-banner");
    if (banner) banner.style.display = "none";
}

// ---- Config Editor ----

const PROVIDER_OPTIONS = ["openai", "anthropic", "deepseek", "google", "gemini", "mistral", "ollama"];
const PROVIDER_KEYS = ["PROVIDER_EXTRACTION", "PROVIDER_SYNTHESIS", "JUDGE_PROVIDER"];
const NUMBER_KEYS = ["TEMPERATURE", "TEMPERATURE_EXTRACTION", "TEMPERATURE_SYNTHESIS", "JUDGE_TEMPERATURE", "MAX_TOKENS_EXTRACTION", "MAX_TOKENS_SYNTHESIS", "EXTRACTION_BATCH_SIZE", "MAX_PARALLEL_EXTRACTIONS", "MAX_RETRIES", "CONCURRENCY"];

const CONFIG_CATEGORIES = [
    {
        id: "models",
        title: "Inference Providers & Models",
        icon: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m18 16 4-4-4-4"/><path d="m6 8-4 4 4 4"/><path d="m14.5 4-5 16"/></svg>`,
        keys: ["PROVIDER_EXTRACTION", "EXTRACTOR_MODEL", "PROVIDER_SYNTHESIS", "SYNTHESIZER_MODEL", "MODEL_EXTRACTION", "MODEL_SYNTHESIS"]
    },
    {
        id: "hyperparameters",
        title: "Generation Hyperparameters",
        icon: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/></svg>`,
        keys: ["TEMPERATURE", "TEMPERATURE_EXTRACTION", "TEMPERATURE_SYNTHESIS", "MAX_TOKENS_EXTRACTION", "MAX_TOKENS_SYNTHESIS", "MAX_TOKENS_JUDGE", "TOKEN_LIMIT_DEFAULT"]
    },
    {
        id: "execution",
        title: "Concurrency & Execution Limits",
        icon: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="18" height="18" x="3" y="3" rx="2"/><path d="m9 8 6 4-6 4Z"/></svg>`,
        keys: ["EXTRACTION_BATCH_SIZE", "MAX_PARALLEL_EXTRACTIONS", "CONCURRENCY", "MAX_RETRIES", "LLM_TIMEOUT", "API_TIMEOUT"]
    },
    {
        id: "grounding",
        title: "Grounding & Adjudication",
        icon: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>`,
        keys: ["LITERATURE_GROUNDING_ENABLED", "JUDGE_PROVIDER", "JUDGE_MODEL", "JUDGE_TEMPERATURE", "LIBRARIAN_TEMPERATURE", "FACT_CHECKER_TEMPERATURE", "CRITIC_TEMPERATURE", "CRITIC_MAX_JSON_RETRIES"]
    }
];

async function loadConfig() {
    try {
        const data = await api(`/api/config/${currentRunDir}`);
        originalConfig = data.config || {};
        renderConfigEditor(originalConfig);
    } catch {
        configContent.innerHTML = '<p class="placeholder-text">Could not load config</p>';
    }
}

function renderConfigEditor(config) {
    const keys = Object.keys(config);
    if (!keys.length) {
        configContent.innerHTML = '<p class="placeholder-text">No config found</p>';
        return;
    }

    const assigned = new Set();
    let sectionsHtml = "";

    function renderItem(k, v) {
        const isMasked = v === "***masked***";
        if (isMasked) {
            return `<div class="config-item config-locked">
                <div class="config-key-wrap">
                    <span class="config-key">${escapeHtml(k)}</span>
                    <span class="lock-pill"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg> Masked</span>
                </div>
                <span class="config-value locked">Stored in .env</span>
            </div>`;
        }
        const isProvider = PROVIDER_KEYS.includes(k);
        const isNumber = NUMBER_KEYS.includes(k);
        let inputHtml;
        if (isProvider) {
            const opts = PROVIDER_OPTIONS.map(o =>
                `<option value="${o}" ${o === v ? "selected" : ""}>${o}</option>`
            ).join("");
            inputHtml = `<select class="config-input config-select" data-key="${escapeHtml(k)}">${opts}</select>`;
        } else if (isNumber) {
            const step = k.includes("TEMPERATURE") ? 'step="0.1"' : "";
            inputHtml = `<input type="number" class="config-input config-number" data-key="${escapeHtml(k)}" value="${escapeHtml(v)}" ${step}>`;
        } else {
            inputHtml = `<input type="text" class="config-input config-text" data-key="${escapeHtml(k)}" value="${escapeHtml(v)}">`;
        }
        return `<div class="config-item editable-config">
            <span class="config-key">${escapeHtml(k)}</span>
            ${inputHtml}
        </div>`;
    }

    // Render defined categories
    for (const cat of CONFIG_CATEGORIES) {
        const catKeys = cat.keys.filter(k => k in config);
        if (catKeys.length > 0) {
            let itemsHtml = "";
            for (const k of catKeys) {
                itemsHtml += renderItem(k, config[k]);
                assigned.add(k);
            }
            sectionsHtml += `<div class="config-category-group" data-cat-id="${cat.id}">
                <div class="config-category-header" role="button" tabindex="0" aria-expanded="true" title="Click to collapse or expand">
                    <div class="cat-header-left">
                        <span class="cat-icon" aria-hidden="true">${cat.icon}</span>
                        <h4 class="cat-title">${escapeHtml(cat.title)}</h4>
                    </div>
                    <svg class="cat-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>
                </div>
                <div class="config-category-items">${itemsHtml}</div>
            </div>`;
        }
    }

    // Remaining keys
    const remainingKeys = keys.filter(k => !assigned.has(k));
    if (remainingKeys.length > 0) {
        let itemsHtml = "";
        for (const k of remainingKeys) {
            itemsHtml += renderItem(k, config[k]);
        }
        sectionsHtml += `<div class="config-category-group" data-cat-id="other">
            <div class="config-category-header" role="button" tabindex="0" aria-expanded="true" title="Click to collapse or expand">
                <div class="cat-header-left">
                    <span class="cat-icon" aria-hidden="true"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg></span>
                    <h4 class="cat-title">Other Runtime Settings</h4>
                </div>
                <svg class="cat-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>
            </div>
            <div class="config-category-items">${itemsHtml}</div>
        </div>`;
    }

    configContent.innerHTML = sectionsHtml;

    // Collapsible accordion interaction
    configContent.querySelectorAll(".config-category-header").forEach(hdr => {
        const toggle = () => {
            const group = hdr.closest(".config-category-group");
            if (group) {
                const isCollapsed = group.classList.toggle("collapsed");
                hdr.setAttribute("aria-expanded", String(!isCollapsed));
            }
        };
        hdr.addEventListener("click", toggle);
        hdr.addEventListener("keydown", (e) => {
            if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                toggle();
            }
        });
    });

    // Track changes
    const banner = document.getElementById("config-unsaved-banner");
    configContent.querySelectorAll(".config-input").forEach(el => {
        el.addEventListener("change", () => {
            configDirty[el.dataset.key] = String(el.value);
            const count = Object.keys(configDirty).length;
            configSaveBtn.style.display = count ? "inline-block" : "none";
            if (banner) banner.style.display = count ? "flex" : "none";
        });
    });
}

async function saveConfig() {
    if (!currentRunDir || !Object.keys(configDirty).length) return;
    try {
        for (const [key, value] of Object.entries(configDirty)) {
            const data = await api(`/api/config/${currentRunDir}`, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ key, value }),
            });
            originalConfig = data.config || originalConfig;
        }
        configDirty = {};
        renderConfigEditor(originalConfig);
        configSaveBtn.style.display = "none";
        const banner = document.getElementById("config-unsaved-banner");
        if (banner) banner.style.display = "none";
        addLog("Config saved", "success");
    } catch (e) {
        addLog("Failed to save config: " + e.message, "error");
    }
}

// ---- Criteria Editor ----

let criteriaTextarea = null;

function updateCriteriaWeightSummary(yamlText) {
    const valEl = document.getElementById("criteria-weight-value");
    const statusEl = document.getElementById("criteria-weight-status");
    if (!valEl || !statusEl) return;

    if (!yamlText) {
        valEl.textContent = "—";
        statusEl.textContent = "Empty";
        statusEl.className = "weight-status-pill";
        return;
    }

    let sum = 0;
    let count = 0;
    const lines = yamlText.split("\n");
    for (const line of lines) {
        const m = line.match(/^\s*weight:\s*([\d.]+)/i);
        if (m) {
            sum += parseFloat(m[1]);
            count++;
        }
    }

    let percent = sum;
    if (sum <= 1.05 && sum > 0) {
        percent = sum * 100;
    }
    percent = Math.round(percent * 10) / 10;

    valEl.textContent = `${percent}% (${count} criteria)`;
    if (Math.abs(percent - 100) < 0.5) {
        statusEl.textContent = "Balanced (100%)";
        statusEl.className = "weight-status-pill balanced";
    } else {
        statusEl.textContent = `Unbalanced (${percent}% ≠ 100%)`;
        statusEl.className = "weight-status-pill unbalanced";
    }
}

async function loadCriteria() {
    if (!currentRunDir) return;
    try {
        const data = await api(`/api/criteria-raw/${currentRunDir}`);
        criteriaEditorWrap.innerHTML = "";
        criteriaTextarea = document.createElement("textarea");
        criteriaTextarea.className = "code-editor";
        criteriaTextarea.value = data.content || "";
        criteriaEditorWrap.appendChild(criteriaTextarea);
        updateCriteriaWeightSummary(data.content || "");
        criteriaTextarea.addEventListener("input", () => {
            updateCriteriaWeightSummary(criteriaTextarea.value);
        });
        criteriaSaveBtn.style.display = "inline-block";
        criteriaReloadBtn.style.display = "inline-block";
        criteriaValidation.textContent = "";
    } catch (e) {
        criteriaEditorWrap.innerHTML = `<p class="placeholder-text">Could not load criteria: ${escapeHtml(e.message)}</p>`;
        criteriaSaveBtn.style.display = "none";
        criteriaReloadBtn.style.display = "none";
        updateCriteriaWeightSummary("");
    }
}

async function saveCriteria() {
    if (!currentRunDir || !criteriaTextarea) return;
    criteriaValidation.textContent = "";
    try {
        await api(`/api/criteria/${currentRunDir}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ content: criteriaTextarea.value }),
        });
        criteriaValidation.textContent = "Saved!";
        criteriaValidation.className = "validation-msg success";
        addLog("Criteria saved", "success");
    } catch (e) {
        criteriaValidation.textContent = e.message;
        criteriaValidation.className = "validation-msg error";
    }
}

async function reloadCriteria() {
    await loadCriteria();
}

// ---- Prompts Editor ----

const PROMPT_TEMPLATE_VARS = {
    "extractor_system.txt": "No template variables (system instructions)",
    "extractor_user.txt": "{domain}, {paper_markdown}, {criterion_name}, {criterion_description}, {scale_definition}",
    "synthesizer_system.txt": "No template variables (system instructions)",
    "synthesizer_user.txt": "{paper_title}, {paper_abstract}, {json_dump_of_extractions}, {weights_table}, {calculated_score}, {calculated_recommendation}",
};

async function loadPrompts() {
    try {
        const data = await api("/api/prompts");
        promptFiles = data.prompts || [];
        promptSelector.innerHTML = "";
        for (const p of promptFiles) {
            const opt = document.createElement("option");
            opt.value = p.filename;
            opt.textContent = p.filename.replace(/\.txt$/, "");
            promptSelector.appendChild(opt);
        }
        if (promptFiles.length > 0) {
            promptSelector.value = promptFiles[0].filename;
            onPromptSelected();
        }
    } catch {
        promptSelector.innerHTML = '<option value="">Error loading prompts</option>';
    }
}

function onPromptSelected() {
    const fn = promptSelector.value;
    if (!fn) return;
    currentPromptFilename = fn;
    const file = promptFiles.find(p => p.filename === fn);
    promptsEditor.value = file ? file.content : "";
    promptVarsHint.textContent = PROMPT_TEMPLATE_VARS[fn] || "";
    promptSaveBtn.disabled = false;
    promptValidation.textContent = "";
}

async function savePrompt() {
    if (!currentPromptFilename) return;
    promptValidation.textContent = "";
    try {
        await api(`/api/prompts/${currentPromptFilename}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ content: promptsEditor.value }),
        });
        // Update local cache
        const idx = promptFiles.findIndex(p => p.filename === currentPromptFilename);
        if (idx >= 0) promptFiles[idx].content = promptsEditor.value;
        promptValidation.textContent = "Saved!";
        promptValidation.className = "validation-msg success";
        addLog(`Prompt '${currentPromptFilename}' saved`, "success");
    } catch (e) {
        promptValidation.textContent = e.message;
        promptValidation.className = "validation-msg error";
    }
}

async function reloadPrompt() {
    try {
        const data = await api("/api/prompts");
        promptFiles = data.prompts || [];
        onPromptSelected();
        promptValidation.textContent = "Reloaded from disk";
        promptValidation.className = "validation-msg success";
    } catch (e) {
        promptValidation.textContent = e.message;
        promptValidation.className = "validation-msg error";
    }
}

// ---- Literature Sources Editor ----

async function loadSources() {
    try {
        const data = await api("/api/literature-sources");
        sourcesEditor.value = data.content || "";
        sourcesValidation.textContent = "";
    } catch {
        sourcesEditor.value = "";
        sourcesEditor.placeholder = "Could not load literature sources";
    }
}

async function saveSources() {
    sourcesValidation.textContent = "";
    try {
        await api("/api/literature-sources", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ content: sourcesEditor.value }),
        });
        sourcesValidation.textContent = "Saved!";
        sourcesValidation.className = "validation-msg success";
        addLog("Literature sources saved", "success");
    } catch (e) {
        sourcesValidation.textContent = e.message;
        sourcesValidation.className = "validation-msg error";
    }
}

async function reloadSources() {
    await loadSources();
}

// ---- Model Costs Editor ----

async function loadCosts() {
    try {
        const data = await api("/api/model-costs");
        costsEditor.value = data.content || "";
        costsValidation.textContent = "";
    } catch {
        costsEditor.value = "";
        costsEditor.placeholder = "Could not load model costs";
    }
}

async function saveCosts() {
    costsValidation.textContent = "";
    try {
        await api("/api/model-costs", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ content: costsEditor.value }),
        });
        costsValidation.textContent = "Saved! Models re-registered with litellm.";
        costsValidation.className = "validation-msg success";
        addLog("Model costs saved and re-registered", "success");
    } catch (e) {
        costsValidation.textContent = e.message;
        costsValidation.className = "validation-msg error";
    }
}

async function reloadCosts() {
    await loadCosts();
}

async function lookupModelCost() {
    const modelName = prompt("Enter model name (e.g. openai/gpt-5.4-nano, anthropic/claude-sonnet-4-6):");
    if (!modelName) return;
    costsLookupResult.textContent = "Looking up...";
    try {
        const data = await api(`/api/model-costs/lookup/${encodeURIComponent(modelName)}`);
        costsLookupResult.innerHTML =
            `<strong>${escapeHtml(data.model)}</strong>: ` +
            `Input $${data.input_cost_per_million}/M, ` +
            `Output $${data.output_cost_per_million}/M` +
            (data.max_input_tokens ? ` | Max in: ${(data.max_input_tokens / 1000).toFixed(0)}K` : "") +
            (data.max_output_tokens ? `, out: ${(data.max_output_tokens / 1000).toFixed(0)}K` : "");
        costsLookupResult.className = "lookup-result success";
    } catch (e) {
        costsLookupResult.textContent = e.message;
        costsLookupResult.className = "lookup-result error";
    }
}

// ---- Judge ----

async function checkJudgeVerdicts() {
    if (!currentRunDir) return;
    try {
        const data = await api(`/api/judge/results/${currentRunDir}`);
        const verdicts = data.verdicts || [];
        if (viewVerdictsBtn) viewVerdictsBtn.style.display = verdicts.length > 0 ? "inline-block" : "none";
    } catch {
        if (viewVerdictsBtn) viewVerdictsBtn.style.display = "none";
    }
}

async function startJudge() {
    if (!currentRunDir) return;
    judgeBtn.disabled = true;
    logContent.innerHTML = "";
    clearStages();

    try {
        const data = await api(`/api/judge/${currentRunDir}`, { method: "POST" });
        currentRunId = data.run_id;
        runStartTime = Date.now();
        connectSSE();
        startElapsedTimer();
        addLog("Judge pipeline started...", "info");
    } catch (e) {
        const msg = e.message || "Unknown error";
        if (msg.includes("2 consolidated") || msg.includes("400")) {
            addLog("Cannot judge: run reviews with at least 2 different models first, then compare.", "warning");
        } else {
            addLog("Judge failed: " + msg, "error");
        }
        judgeBtn.disabled = false;
    }
}

async function showJudgeVerdicts() {
    if (!currentRunDir) return;
    judgeModalBody.innerHTML = "Loading verdicts...";

    try {
        const data = await api(`/api/judge/results/${currentRunDir}`);
        const verdicts = data.verdicts || [];
        if (!verdicts.length) {
            judgeModalBody.innerHTML = '<p class="placeholder-text">No judge verdicts yet. Run "Compare & Judge" first.</p>';
            judgeModal.style.display = "flex";
            return;
        }

        let html = '<table class="results-table judge-table"><thead><tr>';
        html += "<th>Paper</th><th>Judge Decision</th><th>Winner</th><th>Rationale</th><th>Cost</th>";
        html += "</tr></thead><tbody>";

        for (const v of verdicts) {
            const winClass = v.winning_review === "A" ? "rec-accept" :
                             v.winning_review === "B" ? "rec-reject" : "rec-revision";
            html += "<tr>";
            html += `<td>${escapeHtml(v.paper_filename || "")}</td>`;
            html += `<td><span class="rec-badge rec-revision">${escapeHtml(v.judge_recommendation || "")}</span></td>`;
            html += `<td><span class="rec-badge ${winClass}">${escapeHtml(v.winning_review || "")}</span></td>`;
            html += `<td>${escapeHtml(v.judge_rationale || "")}</td>`;
            html += `<td>$${parseFloat(v.judge_cost || 0).toFixed(4)}</td>`;
            html += "</tr>";
        }

        html += "</tbody></table>";
        judgeModalBody.innerHTML = html;
        judgeModal.style.display = "flex";
    } catch (e) {
        judgeModalBody.innerHTML = "Error: " + e.message;
    }
}

// ---- Batch Processing ----

let batchPollInterval = null;

function startBatchPolling() {
    if (batchPollInterval) clearInterval(batchPollInterval);
    batchPollInterval = setInterval(async () => {
        const queueData = await fetchBatchQueue();
        if (queueData && !queueData.active) {
            clearInterval(batchPollInterval);
            batchPollInterval = null;
        }
    }, 1500);
}

function stopBatchPolling() {
    if (batchPollInterval) {
        clearInterval(batchPollInterval);
        batchPollInterval = null;
    }
}

async function fetchBatchQueue() {
    try {
        const data = await api("/api/batch-queue");
        renderBatchQueue(data);
        return data;
    } catch (e) {
        return null;
    }
}

function renderBatchQueue(queueData) {
    if (!queueData) return;
    const items = queueData.items || [];
    const counts = queueData.counts || {};
    const isActive = !!queueData.active;
    batchActive = isActive;

    const setNum = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = val != null ? val : 0;
    };
    setNum("batch-stat-total", queueData.total);
    setNum("batch-stat-queued", counts.queued);
    setNum("batch-stat-running", counts.running);
    setNum("batch-stat-completed", counts.completed);
    setNum("batch-stat-failed", counts.failed);
    setNum("batch-stat-interrupted", counts.interrupted);
    setNum("batch-stat-skipped", counts.duplicate_skipped);

    const doneCount = (counts.completed || 0) + (counts.duplicate_skipped || 0) + (counts.failed || 0) + (counts.interrupted || 0);
    const totalCount = queueData.total || 0;
    const pct = totalCount > 0 ? Math.round((doneCount / totalCount) * 100) : 0;

    const pBars = [document.getElementById("batch-progress-bar")];
    pBars.forEach(pb => {
        if (pb) pb.style.width = pct + "%";
    });

    const bText = document.getElementById("batch-text");
    if (bText) {
        if (isActive) {
            bText.textContent = `Processing batch: ${doneCount}/${totalCount} completed (${pct}%)`;
        } else if (totalCount > 0 && doneCount === totalCount) {
            bText.textContent = `Batch finished (${totalCount} manuscripts processed)`;
        } else if (totalCount > 0) {
            bText.textContent = `Queue paused (${counts.queued || 0} queued, ${counts.failed || 0} failed)`;
        } else {
            bText.textContent = "Queue Idle";
        }
    }

    const bCurrent = document.getElementById("batch-current");
    if (bCurrent) {
        const runningItem = items.find(it => it.status === "running");
        if (runningItem) {
            bCurrent.textContent = `Running #${runningItem.position}: ${runningItem.original_filename} (${runningItem.stage || 'Processing'})`;
        } else {
            bCurrent.textContent = "";
        }
    }

    const updateButtons = (prefix) => {
        const btnResume = document.getElementById(prefix ? `btn-${prefix}-resume` : "btn-batch-resume");
        const btnStop = document.getElementById(prefix ? `btn-${prefix}-stop` : "btn-batch-cancel");
        const btnRetry = document.getElementById(prefix ? `btn-${prefix}-retry` : "btn-batch-retry-failed");
        const btnClear = document.getElementById("btn-batch-clear");

        if (btnResume) {
            btnResume.style.display = (!isActive && (counts.queued || 0) > 0) ? "inline-block" : "none";
        }
        if (btnStop) {
            btnStop.style.display = isActive ? "inline-block" : "none";
        }
        if (btnRetry) {
            btnRetry.style.display = (!isActive && ((counts.failed || 0) > 0 || (counts.interrupted || 0) > 0)) ? "inline-block" : "none";
        }
        if (btnClear && !prefix) {
            btnClear.style.display = (!isActive && ((counts.completed || 0) > 0 || (counts.duplicate_skipped || 0) > 0)) ? "inline-block" : "none";
        }
    };
    updateButtons("");
    updateButtons("upload-batch");

    if (batchBtn) batchBtn.disabled = isActive;
    if (batchStopBtn) batchStopBtn.style.display = isActive ? "inline-block" : "none";

    const uploadBatchSection = document.getElementById("upload-batch-section");
    if (uploadBatchSection) {
        if (items.length > 0) uploadBatchSection.style.display = "block";
    }
    if (batchPanel) {
        if (items.length > 0 || isActive) batchPanel.style.display = "block";
    }

    const tbodyReviews = document.getElementById("batch-queue-tbody");
    const tbodyUpload = document.getElementById("upload-batch-tbody");
    const tbodies = [tbodyReviews, tbodyUpload].filter(Boolean);

    let rowsHtml = "";
    if (items.length === 0) {
        rowsHtml = `<tr><td colspan="8" class="placeholder-text" style="text-align: center; padding: 2rem;">No manuscripts currently staged in batch queue.</td></tr>`;
    } else {
        rowsHtml = items.map(it => {
            const statusClass = `batch-badge-${it.status || 'queued'}`;
            const statusLabel = (it.status || 'queued').replace('_', ' ');

            let scoreRec = "—";
            if (it.overall_score != null || it.final_system_status || it.provisional_recommendation) {
                const parts = [];
                if (it.overall_score != null) parts.push(`Score: <strong>${it.overall_score}</strong>/100`);
                if (it.final_system_status) parts.push(escapeHtml(it.final_system_status));
                else if (it.provisional_recommendation) parts.push(escapeHtml(it.provisional_recommendation));
                scoreRec = parts.join(" · ");
            }

            let errorCell = "—";
            if (it.error_message) {
                errorCell = `<div class="batch-cell-error" title="${escapeHtml(it.error_message)}">${escapeHtml(it.error_message)}</div>`;
            } else if (it.status === "duplicate_skipped") {
                errorCell = `<span style="color:var(--text-secondary); font-size:11px;">Identical paper skipped to reuse analysis</span>`;
            }

            let actionsHtml = "";
            if (it.status === "completed" && it.run_dir) {
                actionsHtml = `<a href="#/analysis/${encodeURIComponent(it.run_dir)}" class="btn btn-sm btn-secondary" style="padding: 3px 8px; font-size: 11px;">View</a>`;
            } else if ((it.status === "failed" || it.status === "interrupted") && !isActive) {
                actionsHtml = `<button type="button" class="btn btn-sm btn-primary btn-retry-item" data-item-id="${escapeHtml(it.item_id)}" style="padding: 3px 8px; font-size: 11px;">Retry</button>`;
            } else if (it.status === "duplicate_skipped" && it.run_dir) {
                actionsHtml = `<a href="#/analysis/${encodeURIComponent(it.run_dir)}" class="btn btn-sm btn-secondary" style="padding: 3px 8px; font-size: 11px;">View Dup</a>`;
            } else {
                actionsHtml = `<span style="color:var(--text-secondary); font-size:11px;">—</span>`;
            }

            let stageProgress = escapeHtml(it.stage || "Queued");
            if (it.status === "running") {
                stageProgress = `<div style="display:flex; flex-direction:column; gap:4px;">
                    <span>${escapeHtml(it.stage || "Processing")}</span>
                    <div class="progress-bar-container" style="height:4px; margin:0;"><div class="progress-bar" style="width:${it.progress_pct || 15}%;"></div></div>
                </div>`;
            }

            return `<tr>
                <td style="font-weight:600;">${it.position || 1}</td>
                <td>
                    <div class="batch-cell-filename">${escapeHtml(it.original_filename || 'Untitled')}</div>
                    ${it.paper_title && it.paper_title !== it.original_filename ? `<div class="batch-cell-title">${escapeHtml(it.paper_title)}</div>` : ''}
                </td>
                <td>
                    ${it.run_dir ? `<span class="batch-cell-rundir">${escapeHtml(it.run_dir)}</span>` : '<span style="color:var(--text-secondary);">—</span>'}
                </td>
                <td><span class="batch-badge ${statusClass}">${escapeHtml(statusLabel)}</span></td>
                <td>${stageProgress}</td>
                <td>${scoreRec}</td>
                <td>${errorCell}</td>
                <td>${actionsHtml}</td>
            </tr>`;
        }).join("");
    }

    tbodies.forEach(tb => {
        tb.innerHTML = rowsHtml;
    });

    document.querySelectorAll(".btn-retry-item").forEach(btn => {
        btn.onclick = async (e) => {
            const itemId = e.currentTarget.getAttribute("data-item-id");
            if (!itemId) return;
            try {
                btn.disabled = true;
                btn.textContent = "Retrying...";
                await api(`/api/batch/items/${encodeURIComponent(itemId)}/retry`, { method: "POST" });
                startBatchPolling();
                await fetchBatchQueue();
            } catch (err) {
                alert("Retry failed: " + err.message);
                btn.disabled = false;
                btn.textContent = "Retry";
            }
        };
    });
}

function setupBatchListeners() {
    const bindBtn = (id, handler) => {
        const el = document.getElementById(id);
        if (el) el.addEventListener("click", handler);
    };

    const handleResume = async () => {
        try {
            await api("/api/batch-start", {
                method: "POST",
                body: JSON.stringify({ mode: modeSelector ? modeSelector.value : "standard" })
            });
            startBatchPolling();
            await fetchBatchQueue();
        } catch (e) {
            alert("Failed to start batch: " + e.message);
        }
    };

    const handleStop = async () => {
        try {
            await api("/api/batch-stop", { method: "POST" });
            await fetchBatchQueue();
        } catch (e) {
            alert("Failed to stop batch: " + e.message);
        }
    };

    const handleRetryFailed = async () => {
        try {
            await api("/api/batch/retry-failed", { method: "POST" });
            startBatchPolling();
            await fetchBatchQueue();
        } catch (e) {
            alert("Failed to retry items: " + e.message);
        }
    };

    const handleClear = async () => {
        try {
            await api("/api/batch/clear", { method: "POST" });
            await fetchBatchQueue();
        } catch (e) {
            alert("Failed to clear finished items: " + e.message);
        }
    };

    bindBtn("btn-batch-resume", handleResume);
    bindBtn("btn-upload-batch-resume", handleResume);
    bindBtn("btn-batch-cancel", handleStop);
    bindBtn("btn-upload-batch-stop", handleStop);
    bindBtn("btn-batch-retry-failed", handleRetryFailed);
    bindBtn("btn-upload-batch-retry", handleRetryFailed);
    bindBtn("btn-batch-clear", handleClear);
}

async function startBatch() {
    batchBtn.disabled = true;
    startBtn.disabled = true;
    batchStopBtn.style.display = "inline-block";
    batchActive = true;

    if (batchPanel) batchPanel.style.display = "block";
    logContent.innerHTML = "";

    try {
        await api("/api/batch-start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ mode: modeSelector.value }),
        });
        addLog(`Batch started in ${modeSelector.value} mode`, "info");
        connectBatchSSE();
        startBatchPolling();
        runStartTime = Date.now();
        startElapsedTimer();
        await fetchBatchQueue();
    } catch (e) {
        addLog("Batch failed: " + e.message, "error");
        finishBatch();
    }
}

function connectBatchSSE() {
    if (eventSource) eventSource.close();
    sseReconnectAttempts = 0;

    eventSource = new EventSource("/api/events/batch");

    eventSource.addEventListener("progress", (e) => {
        sseReconnectAttempts = 0;
        try {
            const evt = JSON.parse(e.data);
            handleEvent(evt);
        } catch {}
    });

    eventSource.addEventListener("ping", () => {
        sseReconnectAttempts = 0;
    });

    eventSource.onerror = () => {
        eventSource.close();
        eventSource = null;
        sseReconnectAttempts++;
        if (sseReconnectAttempts <= SSE_MAX_RECONNECT && batchActive) {
            addLog(`Connection lost — reconnecting (${sseReconnectAttempts}/${SSE_MAX_RECONNECT})...`, "warning");
            setTimeout(() => { if (batchActive) connectBatchSSE(); }, 2000 * sseReconnectAttempts);
        } else if (batchActive) {
            addLog("Connection lost. Refresh the page to reconnect.", "error");
        }
    };
}

async function stopBatch() {
    try {
        await api("/api/batch-stop", { method: "POST" });
        addLog("Batch cancellation requested...", "warning");
        stopBatchPolling();
        await fetchBatchQueue();
    } catch (e) {
        addLog("Stop batch failed: " + e.message, "error");
    }
}

function updateBatchProgress(index, total, dirName) {
    const pct = total > 0 ? (100 * index / total) : 0;
    if (batchProgressBar) batchProgressBar.style.width = pct + "%";
    if (batchText) batchText.textContent = `${index}/${total} directories`;
    if (batchCurrent) batchCurrent.textContent = `Current: ${dirName}`;
}

function renderBatchDirsList(dirs) {
    if (!batchDirsList) return;
    batchDirsList.innerHTML = dirs.map(d =>
        `<span class="batch-dir-item">${escapeHtml(d)}</span>`
    ).join("");
}

function finishBatch() {
    batchActive = false;
    batchBtn.disabled = false;
    startBtn.disabled = !currentRunDir;
    batchStopBtn.style.display = "none";
    if (batchProgressBar) batchProgressBar.style.width = "100%";
    if (batchText) batchText.textContent = "Batch complete";
    if (batchCurrent) batchCurrent.textContent = "";
    if (elapsedInterval) clearInterval(elapsedInterval);
    if (eventSource) { eventSource.close(); eventSource = null; }
    stopBatchPolling();
    fetchBatchQueue();
    if (currentRunDir) loadResults();
}

// Init
document.addEventListener("DOMContentLoaded", init);
