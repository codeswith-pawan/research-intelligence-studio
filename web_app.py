"""
FastAPI Web Application

Serves the dashboard UI and provides REST/SSE API endpoints
for managing and monitoring review runs.
"""

import os
import sys
import json
import time
import asyncio
import argparse
import threading
import glob
import shutil
import hashlib
import csv
import re
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.progress import (
    get_emitter, get_sse_backend, SSEBackend,
    Event, RunStarted, RunCompleted, PaperCompleted, RunProgress,
    StageStarted, StageCompleted, Error, CostUpdate,
    configure_emitter,
)
from core.paper_overview import (
    PaperOverviewData,
    compute_file_sha256,
    validate_overview_payload,
    load_paper_overview_cache,
    save_paper_overview_cache,
    generate_paper_overview,
    OVERVIEW_SECTION_KEYS,
)

# Import llm_wrapper early so custom models are registered with litellm
import core.llm_wrapper  # noqa: F401

def _save_evidence_records(run_dir: str, paper_filename: str, extractions) -> None:
    """Persist extracted evidence and its PDF quote-verification status."""
    output_dir = Path(run_dir) / "outputs" / "evidence"
    output_dir.mkdir(parents=True, exist_ok=True)

    records = []
    for extraction in extractions:
        data = (
            extraction.model_dump(mode="json")
            if hasattr(extraction, "model_dump")
            else extraction
        )
        if not isinstance(data, dict):
            continue

        criterion_id = data.get("criterion_id", "unknown")
        evidence_items = data.get("evidence", []) or []

        for item in evidence_items:
            if not isinstance(item, dict):
                continue

            page_reference = str(item.get("page_reference", "N/A") or "N/A")
            records.append({
                "paper_filename": paper_filename,
                "criterion_id": criterion_id,
                "score": data.get("score"),
                "score_justification": data.get("score_justification", ""),
                "quote": str(item.get("quote", "") or ""),
                "page_reference": page_reference,
                "relevance": str(item.get("relevance", "") or ""),
                "verification_status": item.get(
                    "verification_status",
                    "Verified" if page_reference.startswith("PDF Page ") else "Unverified",
                ),
                "pdf_page_numbers": item.get("pdf_page_numbers", []),
                "verification_note": item.get("verification_note"),
            })

    safe_name = Path(paper_filename).stem
    output_path = output_dir / f"{safe_name}_evidence.json"
    output_path.write_text(json.dumps(records, indent=2, ensure_ascii=False))


app = FastAPI(title="Agentic Paper Review System")

# Static files
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(PROJECT_ROOT, "web", "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


_run_mutation_lock = threading.Lock()


def _is_run_soft_deleted(run_path: str) -> bool:
    """Check if a run directory is soft-deleted via marker file, location, or metadata flag."""
    if ".deleted_reviews" in run_path.split(os.sep):
        return True
    if os.path.isfile(os.path.join(run_path, "soft_deleted.json")):
        return True
    meta_candidates = [
        os.path.join(run_path, "input", "run_metadata.json"),
        os.path.join(run_path, "run_metadata.json"),
    ]
    for mc in meta_candidates:
        if os.path.isfile(mc):
            try:
                with open(mc, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data.get("is_deleted"):
                        return True
            except Exception:
                pass
    return False


def _get_soft_deleted_metadata(run_path: str) -> Optional[Dict[str, Any]]:
    """Get soft-deletion metadata for a run directory if present."""
    marker = os.path.join(run_path, "soft_deleted.json")
    if os.path.isfile(marker):
        try:
            with open(marker, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    meta_candidates = [
        os.path.join(run_path, "input", "run_metadata.json"),
        os.path.join(run_path, "run_metadata.json"),
    ]
    for mc in meta_candidates:
        if os.path.isfile(mc):
            try:
                with open(mc, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data.get("is_deleted"):
                        return {
                            "deleted_at": data.get("deleted_at"),
                            "reason": data.get("deleted_reason", "soft_deleted"),
                        }
            except Exception:
                pass
    return None


def _resolve_run_path(run_dir: str, allow_deleted: bool = False) -> str:
    """Resolve a run directory name to an absolute path, validating it exists and is accessible."""
    if os.path.isabs(run_dir):
        full_path = run_dir
    else:
        full_path = os.path.join(PROJECT_ROOT, run_dir)
    resolved = str(Path(full_path).resolve())
    if not resolved.startswith(str(Path(PROJECT_ROOT).resolve())):
        raise HTTPException(status_code=403, detail="Access denied")
    if not os.path.isdir(resolved):
        raise HTTPException(status_code=404, detail=f"Directory not found: {run_dir}")
    if not allow_deleted and _is_run_soft_deleted(resolved):
        raise HTTPException(
            status_code=404,
            detail=f"Run directory '{run_dir}' is soft-deleted. Restore it to access its reviews.",
        )
    return resolved

# ---------------------------------------------------------------------------
# Run state tracking
# ---------------------------------------------------------------------------

_active_runs: Dict[str, Dict[str, Any]] = {}
_run_history: List[Dict[str, Any]] = []
_cancel_flags: Dict[str, bool] = {}
_batch_state: Dict[str, Any] = {}  # {"active": bool, "dirs": [...], "current_index": int, ...}

BATCH_QUEUE_FILE = os.path.join(PROJECT_ROOT, "batch_queue.json")
_batch_lock = threading.Lock()
_batch_runner_thread: Optional[threading.Thread] = None

# Dedicated Paper Overview job state tracking (independent of batch queue and reviews)
_overview_jobs: Dict[str, Dict[str, Any]] = {}
_overview_lock = threading.Lock()


def _load_batch_queue() -> Dict[str, Any]:
    """Load the persisted batch queue atomically, returning an initialized queue structure on failure."""
    default_queue: Dict[str, Any] = {
        "queue_version": 1,
        "duplicate_policy": "skip",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "items": [],
    }
    if not os.path.exists(BATCH_QUEUE_FILE):
        return default_queue
    try:
        with open(BATCH_QUEUE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
                return data
            return default_queue
    except Exception:
        return default_queue


def _save_batch_queue(queue_data: Dict[str, Any]) -> None:
    """Persist batch queue atomically using a process-isolated temp file and atomic swap."""
    queue_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    tmp_path = f"{BATCH_QUEUE_FILE}.tmp.{os.getpid()}_{uuid.uuid4().hex[:8]}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(queue_data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, BATCH_QUEUE_FILE)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise


def _recover_batch_queue_on_startup() -> None:
    """Recover interrupted queue items after an unexpected server restart."""
    with _batch_lock:
        queue = _load_batch_queue()
        modified = False
        now_iso = datetime.now(timezone.utc).isoformat()
        for item in queue.get("items", []):
            if item.get("status") == "running":
                item["status"] = "interrupted"
                item["stage"] = "Interrupted by server restart"
                item["error_message"] = "Process terminated while running; ready to retry"
                item["finished_at"] = now_iso
                modified = True
        if modified:
            _save_batch_queue(queue)


# Perform startup recovery check
try:
    _recover_batch_queue_on_startup()
except Exception:
    pass


def _broadcast_batch_queue_event(event_type: str, payload: Dict[str, Any]) -> None:
    """Broadcast batch event over SSE to all connected clients."""
    sse = get_sse_backend()
    if sse:
        msg = json.dumps({"event_type": event_type, **payload, "timestamp": time.time()})
        with sse._lock:
            for q in list(sse._listeners):
                try:
                    q.put_nowait(msg)
                except Exception:
                    pass


def _load_progress(progress_file: str, current_config_hash: str, mode: str) -> Dict[str, Dict]:
    """Load progress file, resetting if config or mode changed."""
    if not os.path.exists(progress_file):
        return {"papers": {}}
    try:
        with open(progress_file, "r") as f:
            progress_data = json.load(f)
        stored_mode = progress_data.get("mode", "standard")  # CLI doesn't store mode
        if progress_data.get("config_hash") != current_config_hash or stored_mode != mode:
            return {"papers": {}}
        return progress_data
    except (json.JSONDecodeError, KeyError):
        return {"papers": {}}


def _save_progress(progress_file: str, progress: Dict[str, Dict], config_hash: str, mode: str):
    """Save progress file with config hash and mode."""
    progress_data = {
        "config_hash": config_hash,
        "mode": mode,
        "papers": progress,
        "last_updated": datetime.now().isoformat(),
    }
    with open(progress_file, "w") as f:
        json.dump(progress_data, f, indent=2, default=str)



def _save_additional_analysis_reports(
    markdown_text: str, outputs_dir: str, paper_filename: str
) -> Dict[str, Any]:
    """Save supplemental heuristic analyses without changing review decisions."""
    import re

    report_dir = Path(outputs_dir) / "quality_analysis"
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(paper_filename).name)
    report_dir.mkdir(parents=True, exist_ok=True)

    analyzers = {
        "citation_reference": (
            "agents.citation_reference_analyzer",
            "analyze_citations_and_references",
        ),
        "document_structure": (
            "agents.document_structure_analyzer",
            "analyze_document_structure",
        ),
        "cross_section_consistency": (
            "agents.cross_section_consistency_analyzer",
            "analyze_cross_section_consistency",
        ),
    }

    results = {}
    for name, (module_name, function_name) in analyzers.items():
        try:
            import importlib

            analyzer = getattr(importlib.import_module(module_name), function_name)
            report = analyzer(markdown_text or "")
            report["paper_filename"] = paper_filename
            report["status"] = "completed"
        except Exception as exc:
            report = {
                "paper_filename": paper_filename,
                "analysis_type": name,
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "limitations": [
                    "This supplemental analyzer failed; its results are unavailable."
                ],
            }

        report_path = report_dir / f"{safe_name}.{name}.json"
        if report_path.exists():
            # Preserve previously saved supplemental output; never overwrite it.
            results[name] = {
                "status": "preserved_existing",
                "report_file": str(report_path),
            }
            continue
        try:
            report_path.write_text(
                json.dumps(report, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            results[name] = {
                "status": report["status"],
                "report_file": str(report_path),
            }
        except OSError as exc:
            results[name] = {
                "status": "save_failed",
                "error": f"{type(exc).__name__}: {exc}",
            }

    return results


def _classify_run_status(reviews, papers_total: int, cancelled: bool = False) -> str:
    """Summarize artifact completion separately from editorial review reliability."""
    if cancelled:
        return "cancelled"
    if papers_total <= 0 or not reviews:
        return "failed"
    if len(reviews) < papers_total:
        return "partial"
    if any(getattr(review, "review_status", "complete") != "complete" for review in reviews):
        return "completed_with_warnings"
    return "completed"


def _find_run_dirs(include_deleted: bool = False) -> List[Dict[str, Any]]:
    """Scan for run directories at the project root with rich metadata and deterministic numbering."""
    raw_dirs = []
    for entry in sorted(os.listdir(PROJECT_ROOT)):
        path = os.path.join(PROJECT_ROOT, entry)
        if not (os.path.isdir(path) and os.path.isdir(os.path.join(path, "papers"))):
            continue

        is_deleted = _is_run_soft_deleted(path)
        if is_deleted and not include_deleted:
            continue

        papers = (
            glob.glob(os.path.join(path, "papers", "*.pdf"))
            + glob.glob(os.path.join(path, "papers", "*.md"))
            + glob.glob(os.path.join(path, "papers", "*.docx"))
            + glob.glob(os.path.join(path, "papers", "*.PDF"))
            + glob.glob(os.path.join(path, "papers", "*.DOCX"))
        )
        has_output = os.path.isdir(os.path.join(path, "outputs", "reviews"))

        # Metadata loading / fallback
        meta: Dict[str, Any] = {}
        meta_candidates = [
            os.path.join(path, "input", "run_metadata.json"),
            os.path.join(path, "run_metadata.json"),
        ]
        for mc in meta_candidates:
            if os.path.isfile(mc):
                try:
                    with open(mc, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    break
                except Exception:
                    pass

        orig_filename = meta.get("original_filename")
        if not orig_filename and papers:
            orig_filename = os.path.basename(papers[0])
        orig_filename = orig_filename or "Untitled Manuscript"

        # Timestamp parsing: directory name, metadata, or mtime
        dt = None
        m = re.search(r"run_(?:upload|validation)_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})", entry)
        if m:
            try:
                dt = datetime(int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5]), int(m[6]))
            except Exception:
                pass
        if not dt and meta.get("uploaded_at"):
            try:
                iso_str = meta["uploaded_at"].replace("Z", "+00:00")
                dt = datetime.fromisoformat(iso_str)
            except Exception:
                pass
        if not dt:
            dt = datetime.fromtimestamp(os.path.getmtime(path))

        paper_title = meta.get("paper_title_extracted")
        overall_score: Optional[float] = None
        final_system_status: Optional[str] = None

        # Check CSV reports
        csvs = sorted(glob.glob(os.path.join(path, "outputs", "reports", "*.csv")), reverse=True)
        if csvs:
            try:
                with open(csvs[0], "r", encoding="utf-8", errors="replace") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        raw_title = row.get("title", "").strip().strip("*").strip()
                        if raw_title and not paper_title:
                            paper_title = raw_title
                        sc = row.get("overall_score")
                        if sc is not None and sc not in ("", "None", "N/A"):
                            try:
                                overall_score = float(sc)
                            except ValueError:
                                pass
                        rev_st = str(row.get("review_status", "")).lower()
                        if rev_st == "failed":
                            final_system_status = "Failed"
                        else:
                            final_system_status = row.get("final_system_status") or row.get("recommendation")
                        break
            except Exception:
                pass

        # Check progress.json for review_status
        prog_file = os.path.join(path, "progress.json")
        if os.path.isfile(prog_file):
            try:
                with open(prog_file, "r", encoding="utf-8") as pf:
                    prog_data = json.load(pf)
                    for p_data in prog_data.get("papers", {}).values():
                        p_rev = p_data.get("review", {})
                        if p_rev.get("review_status") == "failed":
                            final_system_status = "Failed"
            except Exception:
                pass

        # Check review Markdown if paper_title not yet found or status check
        revs = sorted(glob.glob(os.path.join(path, "outputs", "reviews", "*.md")), reverse=True)
        if revs:
            try:
                with open(revs[0], "r", encoding="utf-8", errors="replace") as f:
                    for _ in range(25):
                        line = f.readline()
                        if not line:
                            break
                        if line.startswith("# ") and not paper_title:
                            m_title = re.sub(
                                r"^#\s+(?:Comprehensive|Scientific Peer)?\s*Review:\s*",
                                "",
                                line.strip(),
                            )
                            m_title = m_title.strip("*# ")
                            if m_title:
                                paper_title = m_title
                        if "Review Status:" in line and "FAILED" in line.upper():
                            final_system_status = "Failed"
            except Exception:
                pass

        # Paper title: Only use real extracted title from metadata, CSV, or Markdown review.
        # DO NOT fabricate or slug-derive a faux paper title from filename (Requirement 3).
        if not paper_title:
            paper_title = None

        # Determine review status
        if entry in _active_runs:
            status = "in_progress"
            status_desc = f"In Progress ({_active_runs[entry].get('stage', 'Running')})"
        elif final_system_status == "Failed":
            status = "failed"
            status_desc = "Failed"
            overall_score = None
        elif has_output:
            status = "completed"
            status_desc = final_system_status or "Completed"
        else:
            status = "ready"
            status_desc = "Ready for Analysis"

        upload_date_str = dt.strftime("%d %b %Y, %H:%M")

        # Grouping key for deterministic numbering:
        # Prioritize real extracted paper title if available, otherwise file hash, otherwise normalized original filename
        if paper_title:
            norm_key = "title_" + re.sub(r"[^a-z0-9]+", "", paper_title.lower())
        elif meta.get("file_hash_sha256"):
            norm_key = "hash_" + meta["file_hash_sha256"]
        else:
            norm_key = "file_" + re.sub(r"[^a-z0-9]+", "", orig_filename.lower())

        del_meta = _get_soft_deleted_metadata(path) if is_deleted else None
        raw_dirs.append({
            "name": entry,
            "path": path,
            "paper_count": len(papers),
            "has_output": has_output,
            "original_filename": orig_filename,
            "safe_filename": os.path.basename(papers[0]) if papers else orig_filename,
            "paper_title": paper_title,
            "upload_timestamp": dt.isoformat(),
            "upload_date_str": upload_date_str,
            "status": status,
            "status_desc": status_desc,
            "final_system_status": final_system_status,
            "overall_score": overall_score,
            "sha256": meta.get("file_hash_sha256", ""),
            "norm_key": norm_key,
            "is_deleted": is_deleted,
            "deleted_at": del_meta.get("deleted_at") if del_meta else None,
        })

    # Group runs for deterministic numbering (chronological within paper)
    groups = defaultdict(list)
    for r in raw_dirs:
        groups[r["norm_key"]].append(r)

    for norm_k, run_group in groups.items():
        run_group.sort(key=lambda x: x["upload_timestamp"])
        total_in_group = len(run_group)
        for idx, r in enumerate(run_group):
            r["run_number"] = idx + 1
            r["total_runs_for_paper"] = total_in_group
            r["run_label"] = f"Run {idx + 1} of {total_in_group}"
            # Original filename is the primary identity (Requirement 3)
            r["display_name"] = f"{r['original_filename']} · {r['run_label']}"

            score_str = (
                f"Score {r['overall_score']:.1f}/100"
                if r["overall_score"] is not None
                else ""
            )
            status_parts = [p for p in [r["upload_date_str"], r["status_desc"], score_str] if p]
            if r["paper_title"] and r["paper_title"].lower() != r["original_filename"].lower():
                r["formatted_summary"] = f"{r['original_filename']} ({r['paper_title']}) · {r['run_label']}"
            else:
                r["formatted_summary"] = f"{r['original_filename']} · {r['run_label']}"
            r["formatted_secondary"] = " · ".join(status_parts)

    # Sort all runs descending (newest first) for default UI view
    raw_dirs.sort(key=lambda x: x["upload_timestamp"], reverse=True)
    return raw_dirs


def _find_deleted_run_dirs() -> List[Dict[str, Any]]:
    """Scan exclusively for soft-deleted run directories."""
    all_runs = _find_run_dirs(include_deleted=True)
    return [r for r in all_runs if r.get("is_deleted")]


def _check_sha256_duplicate(content_hash: str, exclude_run_dir: str = "") -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Check if content hash matches an existing review run or a queued/completed batch item."""
    # 1. Check existing runs on filesystem
    all_runs = _find_run_dirs(include_deleted=False)
    for run in all_runs:
        if exclude_run_dir and run["name"] == exclude_run_dir:
            continue
        papers_dir = os.path.join(run["path"], "papers")
        if not os.path.isdir(papers_dir):
            continue
        for fname in os.listdir(papers_dir):
            if os.path.splitext(fname)[1].lower() not in {".pdf", ".docx"}:
                continue
            fpath = os.path.join(papers_dir, fname)
            if not os.path.isfile(fpath):
                continue
            try:
                with open(fpath, "rb") as f:
                    if hashlib.sha256(f.read()).hexdigest() == content_hash:
                        return True, {
                            "run_dir": run["name"],
                            "filename": fname,
                            "original_filename": run.get("original_filename", fname),
                            "paper_title": run.get("paper_title", fname),
                            "upload_date_str": run.get("upload_date_str", "Earlier run"),
                            "run_label": run.get("run_label", "Run 1"),
                            "final_system_status": run.get("final_system_status") or "Completed",
                            "overall_score": run.get("overall_score"),
                            "recommendation": run.get("provisional_recommendation") or run.get("recommendation"),
                        }
            except OSError:
                continue

    # 2. Check batch queue items
    queue = _load_batch_queue()
    for item in queue.get("items", []):
        if item.get("sha256") == content_hash and item.get("status") not in {"failed", "interrupted"}:
            return True, {
                "run_dir": item.get("run_dir"),
                "filename": item.get("safe_filename"),
                "original_filename": item.get("original_filename"),
                "paper_title": item.get("paper_title"),
                "upload_date_str": item.get("created_at"),
                "run_label": f"Batch Item #{item.get('position', 1)}",
                "final_system_status": item.get("final_system_status") or item.get("status"),
                "overall_score": item.get("overall_score"),
                "recommendation": item.get("provisional_recommendation"),
            }

    return False, None


def _load_config_safe(run_dir: str) -> Dict[str, Any]:
    """Load config for a run directory, masking API keys."""
    env_path = os.path.join(run_dir, "input", ".env")
    config_data: Dict[str, Any] = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    if "KEY" in key.upper() or "SECRET" in key.upper():
                        config_data[key] = "***masked***"
                    else:
                        # Ensure value is always a plain string
                        config_data[key] = str(value)
    return config_data


def _load_criteria(run_dir: str) -> Dict[str, Any]:
    """Load criteria.yaml for a run directory."""
    criteria_path = os.path.join(run_dir, "input", "criteria.yaml")
    if not os.path.exists(criteria_path):
        # Try default
        criteria_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "criteria.yaml")
    if not os.path.exists(criteria_path):
        return {}
    import yaml
    with open(criteria_path) as f:
        return yaml.safe_load(f) or {}


def _get_reviews(run_dir: str) -> List[Dict[str, Any]]:
    """Get list of review files for a run directory."""
    reviews_dir = os.path.join(run_dir, "outputs", "reviews")
    if not os.path.isdir(reviews_dir):
        return []
    reviews = []
    for f in sorted(glob.glob(os.path.join(reviews_dir, "*.md")), reverse=True):
        reviews.append({
            "filename": os.path.basename(f),
            "path": f,
            "size": os.path.getsize(f),
            "modified": datetime.fromtimestamp(os.path.getmtime(f)).isoformat(),
        })
    return reviews


def _parse_csv_results(run_dir: str) -> List[Dict[str, Any]]:
    """Parse the latest consolidated CSV into JSON."""
    reports_dir = os.path.join(run_dir, "outputs", "reports")
    if not os.path.isdir(reports_dir):
        return []
    # Match both naming conventions: consolidated_reviews_*.csv and report_consolidated_*.csv
    csv_files = (
        sorted(glob.glob(os.path.join(reports_dir, "consolidated_reviews_*.csv")), reverse=True)
        or sorted(glob.glob(os.path.join(reports_dir, "report_consolidated_*.csv")), reverse=True)
    )
    if not csv_files:
        return []
    try:
        import pandas as pd
        df = pd.read_csv(csv_files[0])
        return df.fillna("").to_dict(orient="records")
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Pipeline runner (background thread)
# ---------------------------------------------------------------------------

def _run_pipeline(run_dir: str, run_id: str, mode: str, config_overrides: Dict[str, Any]):
    """Execute the review pipeline in a background thread."""
    from core.config_loader import Config
    from core.paper_ingestor import load_ingestion_cache, save_ingestion_cache, ingest_directory
    from utilities.helpers import setup_logging, get_config_hash

    emitter = get_emitter()
    setup_logging()

    start_time = time.time()
    _cancel_flags[run_id] = False
    papers = []
    final_reviews = []
    total_cost = 0.0
    run_completed_emitted = False

    try:
        dirs = {
            "run_dir": run_dir,
            "papers_dir": os.path.join(run_dir, "papers"),
            "input_dir": os.path.join(run_dir, "input"),
            "outputs_dir": os.path.join(run_dir, "outputs"),
            "reports_dir": os.path.join(run_dir, "outputs", "reports"),
            "reviews_dir": os.path.join(run_dir, "outputs", "reviews"),
        }
        for d in dirs.values():
            os.makedirs(d, exist_ok=True)

        # Apply config overrides to .env
        if config_overrides:
            env_path = os.path.join(dirs["input_dir"], ".env")
            env_vars = {}
            if os.path.exists(env_path):
                with open(env_path) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            env_vars[k] = v
            env_vars.update(config_overrides)
            with open(env_path, "w") as f:
                for k, v in env_vars.items():
                    if not k.endswith("_API_KEY"):
                        clean_v = str(v).replace("\n", "").replace("\r", "")
                        f.write(f"{k}={clean_v}\n")

        config = Config(config_path=dirs["input_dir"])
        config_hash = get_config_hash(config)

        # Load progress for idempotent resume
        progress_file = os.path.join(run_dir, "progress.json")
        progress_data = _load_progress(progress_file, config_hash, mode)
        progress_papers = progress_data.get("papers", {})

        # Ingest papers
        cache_file = os.path.join(run_dir, "ingestion_cache.json")
        ingestion_cache = load_ingestion_cache(cache_file)
        papers, cache_was_updated = ingest_directory(dirs["papers_dir"], ingestion_cache, cache_file)
        if cache_was_updated:
            save_ingestion_cache(ingestion_cache, cache_file)

        if not papers:
            emitter.emit(Event(event_type="error", stage_name="ingest", message="No papers found", recoverable=False))
            return

        emitter.emit(RunStarted(run_dir=run_dir, mode=mode, paper_count=len(papers)))

        # Select pipeline
        if mode == "literature":
            from agents.agent_librarian import create_baseline_reference
            from agents.agent_reader import process_paper_extractions as process_lit
            from agents.agent_fact_checker import run_fact_checks
            from agents.agent_critic import synthesize_grounded_review
            from agents.agent_extractor import process_paper_extractions
            from agents.agent_synthesizer import synthesize_review
            from run_review_with_dir_literature import enhance_review_with_literature
            from utilities.output_generator import save_review_markdown, save_consolidated_csv
            from utilities.helpers import load_yaml_config
            from core.data_models import GroundedReview, LiteratureContext

            literature_config = load_yaml_config("config/literature_sources.yaml")
        else:
            from agents.agent_extractor import process_paper_extractions
            from agents.agent_synthesizer import synthesize_review
            from utilities.output_generator import save_review_markdown, save_consolidated_csv

        final_reviews = []
        total_cost = 0.0

        for i, paper in enumerate(papers, 1):
            if _cancel_flags.get(run_id, False):
                emitter.emit(Event(event_type="error", stage_name="cancel", message="Run cancelled by user", recoverable=True))
                break

            # Skip already-processed papers (idempotent resume)
            if paper.filename in progress_papers:
                review_data = progress_papers[paper.filename]["review"]
                if mode == "literature":
                    from core.data_models import GroundedReview
                    review = GroundedReview.model_validate(review_data)
                else:
                    from core.data_models import Review
                    review = Review.model_validate(review_data)
                final_reviews.append(review)
                paper_cost = progress_papers[paper.filename].get("cost", 0.0)
                total_cost += paper_cost

                emitter.emit(PaperCompleted(
                    paper_filename=paper.filename,
                    score=review.overall_score,
                    recommendation=review.recommendation,
                    cost=paper_cost,
                    duration_s=0.0,
                    review_status=review.review_status,
                    successful_criteria=review.successful_criteria,
                    total_criteria=review.total_criteria,
                    verified_evidence_count=review.verified_evidence_count,
                    unverified_evidence_count=review.unverified_evidence_count,
                ))
                elapsed = time.time() - start_time
                est_remaining = (elapsed / i * (len(papers) - i)) if i > 0 else 0
                emitter.emit(RunProgress(
                    papers_done=len(final_reviews), papers_total=len(papers),
                    elapsed_s=elapsed, estimated_remaining_s=est_remaining,
                ))
                emitter.emit(CostUpdate(paper_cost=paper_cost, total_cost=total_cost))
                continue

            paper_start = time.time()

            # Generate a separate academic section report without affecting review.
            emitter.emit(StageStarted(
                stage_name="Section Analysis",
                paper_filename=paper.filename,
            ))
            section_start = time.time()
            try:
                from agents.academic_section_analyzer import save_section_analysis

                report_path = save_section_analysis(
                    markdown_text=paper.content_markdown,
                    output_dir=dirs["outputs_dir"],
                    paper_filename=paper.filename,
                )
                emitter.emit(StageCompleted(
                    stage_name="Section Analysis",
                    duration_s=time.time() - section_start,
                    result_summary=f"Section report generated: {os.path.basename(report_path)}",
                ))
            except Exception as e:
                emitter.emit(Error(
                    stage_name="Section Analysis",
                    message=str(e),
                    recoverable=True,
                ))

            # Supplemental heuristic analyses are stored separately and never
            # modify the main review score or recommendation.
            emitter.emit(StageStarted(
                stage_name="Supplemental Analysis",
                paper_filename=paper.filename,
            ))
            supplemental_start = time.time()
            supplemental_results = {}
            try:
                supplemental_results = _save_additional_analysis_reports(
                    markdown_text=paper.content_markdown,
                    outputs_dir=dirs["outputs_dir"],
                    paper_filename=paper.filename,
                )
            except Exception as exc:
                # Supplemental checks are optional and must not stop the main review.
                emitter.emit(Error(
                    stage_name="Supplemental Analysis",
                    message=f"{type(exc).__name__}: {exc}",
                    recoverable=True,
                ))
            available_count = sum(
                1 for result in supplemental_results.values()
                if result.get("status") in {"completed", "preserved_existing"}
            )
            emitter.emit(StageCompleted(
                stage_name="Supplemental Analysis",
                duration_s=time.time() - supplemental_start,
                result_summary=(
                    f"{available_count}/3 supplemental reports available"
                ),
            ))

            if mode == "literature":
                # Stage 1: Librarian
                emitter.emit(StageStarted(stage_name="Librarian", paper_filename=paper.filename))
                t0 = time.time()
                baseline = None
                try:
                    baseline = create_baseline_reference(paper, config)
                except Exception as e:
                    emitter.emit(Error(stage_name="Librarian", message=str(e)))
                emitter.emit(StageCompleted(stage_name="Librarian", duration_s=time.time()-t0,
                                           result_summary=f"{len(baseline.baseline_papers) if baseline else 0} papers"))

                # Stage 2: Reader/Extractor
                emitter.emit(StageStarted(stage_name="Extraction", paper_filename=paper.filename))
                t0 = time.time()
                if baseline:
                    extractions = process_lit(paper, config, baseline=baseline)
                else:
                    extractions = process_paper_extractions(paper, config)
                emitter.emit(StageCompleted(stage_name="Extraction", duration_s=time.time()-t0,
                                           result_summary=f"{len(extractions)} criteria"))

                if not extractions:
                    emitter.emit(Error(stage_name="Extraction", message=f"No extractions for {paper.filename} — skipping", recoverable=True))
                    continue

                # Stage 3: Fact-Checker
                fact_checks = []
                if baseline:
                    emitter.emit(StageStarted(stage_name="Fact-Check", paper_filename=paper.filename))
                    t0 = time.time()
                    try:
                        fact_checks = run_fact_checks(extractions, config.get_criteria(), config, literature_config) or []
                    except Exception:
                        pass
                    emitter.emit(StageCompleted(stage_name="Fact-Check", duration_s=time.time()-t0,
                                               result_summary=f"{len(fact_checks)} checks"))

                # Stage 4: Two-phase synthesis (standard review + literature enhancement)
                emitter.emit(StageStarted(stage_name="Synthesis", paper_filename=paper.filename))
                t0 = time.time()
                base_review = synthesize_review(paper, extractions, config)
                if base_review:
                    review = enhance_review_with_literature(
                        base_review=base_review, paper=paper, baseline=baseline,
                        fact_checks=fact_checks, extractions=extractions, config=config,
                    )
                else:
                    review = synthesize_grounded_review(paper, extractions, config, baseline=baseline, fact_checks=fact_checks)
                emitter.emit(StageCompleted(stage_name="Synthesis", duration_s=time.time()-t0,
                                           result_summary=f"Score: {review.overall_score:.1f}" if review and review.overall_score is not None else "Failed"))
            else:
                # Standard pipeline
                emitter.emit(StageStarted(stage_name="Extraction", paper_filename=paper.filename))
                t0 = time.time()
                extractions = process_paper_extractions(paper, config)
                if extractions:
                    _save_evidence_records(run_dir, paper.filename, extractions)
                emitter.emit(StageCompleted(stage_name="Extraction", duration_s=time.time()-t0,
                                           result_summary=f"{len(extractions)} criteria"))

                if not extractions:
                    emitter.emit(Error(
                        stage_name="Extraction",
                        message=f"No criterion passed validation for {paper.filename}; saving a diagnostic report.",
                        recoverable=True,
                    ))

                emitter.emit(StageStarted(stage_name="Synthesis", paper_filename=paper.filename))
                t0 = time.time()
                review = synthesize_review(paper, extractions, config)
                emitter.emit(StageCompleted(stage_name="Synthesis", duration_s=time.time()-t0,
                                           result_summary=f"Score: {review.overall_score:.1f}" if review and review.overall_score is not None else "Failed"))

            if not review:
                emitter.emit(Error(
                    stage_name="Synthesis",
                    message=f"No review artifact was produced for {paper.filename}.",
                    recoverable=True,
                ))
                elapsed = time.time() - start_time
                emitter.emit(RunProgress(
                    papers_done=len(final_reviews),
                    papers_total=len(papers),
                    elapsed_s=elapsed,
                ))
                continue

            # Save review
            paper_base = os.path.splitext(paper.filename)[0]
            ext_model = review.extractor_model_used.replace("/", "_")
            syn_model = review.synthesizer_model_used.replace("/", "_")
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            review_fn = f"{paper_base}_{ext_model}_{syn_model}_{ts}.md"
            output_path = os.path.join(dirs["reviews_dir"], review_fn)
            save_review_markdown(review=review, output_path=output_path, paper=paper, config=config)

            paper_duration = time.time() - paper_start
            total_cost += review.total_cost

            final_reviews.append(review)
            emitter.emit(PaperCompleted(
                paper_filename=paper.filename,
                score=review.overall_score,
                recommendation=review.recommendation,
                cost=review.total_cost,
                duration_s=paper_duration,
                review_status=review.review_status,
                successful_criteria=review.successful_criteria,
                total_criteria=review.total_criteria,
                verified_evidence_count=review.verified_evidence_count,
                unverified_evidence_count=review.unverified_evidence_count,
            ))

            elapsed = time.time() - start_time
            avg_per_paper = elapsed / i
            remaining = avg_per_paper * (len(papers) - i)
            emitter.emit(RunProgress(
                papers_done=len(final_reviews), papers_total=len(papers),
                elapsed_s=elapsed, estimated_remaining_s=remaining,
            ))
            emitter.emit(CostUpdate(paper_cost=review.total_cost, total_cost=total_cost))

            # Save progress for idempotent resume
            progress_papers[paper.filename] = {
                "review": review.model_dump(),
                "cost": review.total_cost,
                "timestamp": datetime.now().isoformat(),
            }
            _save_progress(progress_file, progress_papers, config_hash, mode)

        # Save consolidated CSV
        if final_reviews:
            save_consolidated_csv(final_reviews, dirs["reports_dir"])

        total_time = time.time() - start_time
        run_status = _classify_run_status(
            final_reviews,
            len(papers),
            cancelled=bool(_cancel_flags.get(run_id, False)),
        )
        emitter.emit(RunCompleted(
            total_papers=len(final_reviews),
            papers_total=len(papers),
            papers_failed=max(0, len(papers) - len(final_reviews)),
            status=run_status,
            total_cost=total_cost,
            total_time_s=total_time,
            output_dir=dirs["outputs_dir"],
        ))
        run_completed_emitted = True

    except Exception as e:
        import traceback
        emitter.emit(Error(stage_name="pipeline", message=f"{type(e).__name__}: {e}", recoverable=False))
        traceback.print_exc()
    finally:
        if not run_completed_emitted:
            emitter.emit(RunCompleted(
                total_papers=len(final_reviews),
                papers_total=len(papers),
                papers_failed=max(0, len(papers) - len(final_reviews)),
                status="cancelled" if _cancel_flags.get(run_id, False) else "failed",
                total_cost=total_cost,
                total_time_s=time.time() - start_time,
                output_dir=os.path.join(run_dir, "outputs"),
            ))
        _active_runs.pop(run_id, None)
        _cancel_flags.pop(run_id, None)


# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    with open(os.path.join(STATIC_DIR, "index.html")) as f:
        return f.read()


@app.get("/api/runs")
async def list_runs():
    return {"runs": _find_run_dirs()}


@app.get("/api/runs/{run_dir}/metadata")
async def get_run_metadata(run_dir: str):
    """Return enriched paper identity and run metadata for a specific run directory."""
    target_name = os.path.basename(run_dir)
    all_runs = _find_run_dirs()
    for r in all_runs:
        if r["name"] == target_name:
            return r
    raise HTTPException(status_code=404, detail=f"Run directory '{run_dir}' not found.")


def _validate_run_dir_name(run_dir: str) -> str:
    """Validate that run_dir is a safe basename and resolves to a valid run directory under PROJECT_ROOT."""
    if not run_dir or not isinstance(run_dir, str):
        raise HTTPException(status_code=400, detail="Run directory name required")
    # Disallow path traversal characters
    safe_name = Path(run_dir).name
    if safe_name != run_dir or "/" in run_dir or "\\" in run_dir or ".." in run_dir:
        raise HTTPException(status_code=400, detail="Invalid run directory name: path traversal rejected")
    full_path = os.path.join(PROJECT_ROOT, safe_name)
    resolved = str(Path(full_path).resolve())
    root_resolved = str(Path(PROJECT_ROOT).resolve())
    if not (resolved == root_resolved or resolved.startswith(root_resolved + os.sep)):
        raise HTTPException(status_code=403, detail="Access denied: path outside project root")
    if not os.path.isdir(resolved):
        raise HTTPException(status_code=404, detail=f"Run directory not found: {safe_name}")
    if not os.path.isdir(os.path.join(resolved, "papers")):
        raise HTTPException(status_code=400, detail=f"Target directory is not a paper review run: {safe_name}")
    return safe_name


@app.get("/api/deleted-runs")
async def list_deleted_runs():
    """List all soft-deleted runs with original metadata for the recovery interface."""
    return {"deleted_runs": _find_deleted_run_dirs()}


@app.post("/api/runs/{run_dir:path}/delete")
async def soft_delete_run(run_dir: str):
    """Soft-delete a run directory. Preserves all artifacts on disk and marks run as recoverable."""
    safe_name = _validate_run_dir_name(run_dir)
    target_path = os.path.join(PROJECT_ROOT, safe_name)

    # Invariant 9: Active run protection
    if safe_name in _active_runs:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete active run: review is currently in progress ({_active_runs[safe_name].get('stage', 'Running')})"
        )

    with _run_mutation_lock:
        if _is_run_soft_deleted(target_path):
            raise HTTPException(status_code=400, detail=f"Run '{safe_name}' is already soft-deleted")

        deleted_at = datetime.now(timezone.utc).isoformat()
        trash_payload = {
            "run_dir": safe_name,
            "is_deleted": True,
            "deleted_at": deleted_at,
            "deleted_by": "user",
            "reason": "soft_deleted_from_ui",
        }

        # Atomic write of soft_deleted.json marker
        marker_tmp = os.path.join(target_path, "soft_deleted.json.tmp")
        marker_target = os.path.join(target_path, "soft_deleted.json")
        try:
            with open(marker_tmp, "w", encoding="utf-8") as f:
                json.dump(trash_payload, f, indent=2)
            os.replace(marker_tmp, marker_target)
        except Exception as e:
            if os.path.exists(marker_tmp):
                try: os.remove(marker_tmp)
                except Exception: pass
            raise HTTPException(status_code=500, detail=f"Failed to write deletion marker: {str(e)}")

        # Atomic update of run_metadata.json if present
        meta_candidates = [
            os.path.join(target_path, "input", "run_metadata.json"),
            os.path.join(target_path, "run_metadata.json"),
        ]
        for mc in meta_candidates:
            if os.path.isfile(mc):
                try:
                    with open(mc, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    meta["is_deleted"] = True
                    meta["deleted_at"] = deleted_at
                    meta_tmp = mc + ".tmp"
                    with open(meta_tmp, "w", encoding="utf-8") as f:
                        json.dump(meta, f, indent=2)
                    os.replace(meta_tmp, mc)
                except Exception:
                    pass

    return {
        "status": "soft_deleted",
        "run_dir": safe_name,
        "deleted_at": deleted_at,
        "message": f"Run '{safe_name}' soft-deleted successfully. It can be restored at any time.",
    }


@app.post("/api/runs/{run_dir:path}/restore")
async def restore_run(run_dir: str):
    """Restore a previously soft-deleted run directory."""
    safe_name = _validate_run_dir_name(run_dir)
    target_path = os.path.join(PROJECT_ROOT, safe_name)

    with _run_mutation_lock:
        if not _is_run_soft_deleted(target_path):
            raise HTTPException(status_code=400, detail=f"Run '{safe_name}' is not soft-deleted")

        # Remove soft_deleted.json marker
        marker_file = os.path.join(target_path, "soft_deleted.json")
        if os.path.isfile(marker_file):
            try:
                os.remove(marker_file)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to remove deletion marker: {str(e)}")

        # Atomic update of run_metadata.json if present
        meta_candidates = [
            os.path.join(target_path, "input", "run_metadata.json"),
            os.path.join(target_path, "run_metadata.json"),
        ]
        for mc in meta_candidates:
            if os.path.isfile(mc):
                try:
                    with open(mc, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    meta["is_deleted"] = False
                    meta.pop("deleted_at", None)
                    meta_tmp = mc + ".tmp"
                    with open(meta_tmp, "w", encoding="utf-8") as f:
                        json.dump(meta, f, indent=2)
                    os.replace(meta_tmp, mc)
                except Exception:
                    pass

    return {
        "status": "restored",
        "run_dir": safe_name,
        "message": f"Run '{safe_name}' restored successfully to the active archive.",
    }


def _stage_uploaded_paper(
    content: bytes,
    original_name: str,
    base_run_dir: str = "",
    force_new: bool = False,
    duplicate_policy: str = "skip",
) -> Dict[str, Any]:
    """Validate, deduplicate, and stage an uploaded paper into an isolated run directory."""
    original_name = os.path.basename(original_name or "uploaded_paper.pdf")
    extension = os.path.splitext(original_name)[1].lower()

    if extension not in {".pdf", ".docx"}:
        return {"success": False, "error": "Only PDF and DOCX files are supported."}

    if not content:
        return {"success": False, "error": "The uploaded file is empty."}
    if len(content) > 20 * 1024 * 1024:
        return {"success": False, "error": "File exceeds the 20 MB limit."}

    if extension == ".pdf" and not content.startswith(b"%PDF-"):
        return {"success": False, "error": "The uploaded file does not appear to be a valid PDF."}
    if extension == ".docx" and not content.startswith(b"PK"):
        return {"success": False, "error": "The uploaded file does not appear to be a valid DOCX."}

    content_hash = hashlib.sha256(content).hexdigest()

    # Reuse an existing run if exact same content was uploaded before,
    # unless force_new=True or duplicate_policy=="force_new".
    if not force_new and duplicate_policy != "force_new":
        is_dup, dup_info = _check_sha256_duplicate(content_hash)
        if is_dup and dup_info:
            return {
                "success": True,
                "is_duplicate": True,
                "content_hash": content_hash,
                "content_len": len(content),
                "original_filename": original_name,
                "safe_filename": dup_info.get("filename") or original_name,
                "paper_title": dup_info.get("paper_title") or original_name,
                "run_dir": dup_info.get("run_dir") or "",
                "upload_date_str": dup_info.get("upload_date_str", "Earlier run"),
                "run_label": dup_info.get("run_label", "Run 1"),
                "final_system_status": dup_info.get("final_system_status") or "Completed",
                "overall_score": dup_info.get("overall_score"),
                "recommendation": dup_info.get("recommendation"),
                "message": f"Exact manuscript matches previous run: {dup_info.get('run_dir')}",
            }

    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", original_name)
    if not safe_name or safe_name in {".", ".."}:
        safe_name = "uploaded_paper" + extension

    source_env = None
    source_input_dir = None
    if base_run_dir.strip():
        source_run_dir = _resolve_run_path(base_run_dir.strip())
        source_input_dir = os.path.join(source_run_dir, "input")
        candidate_env = os.path.join(source_input_dir, ".env")
        if os.path.isfile(candidate_env):
            source_env = candidate_env

    run_name = f"run_upload_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    run_dir = os.path.join(PROJECT_ROOT, run_name)
    papers_dir = os.path.join(run_dir, "papers")
    input_dir = os.path.join(run_dir, "input")
    prompts_dir = os.path.join(input_dir, "prompts")

    try:
        os.makedirs(papers_dir, exist_ok=False)
        os.makedirs(prompts_dir, exist_ok=True)

        if source_env:
            shutil.copy2(source_env, os.path.join(input_dir, ".env"))
        else:
            global_env = os.path.join(PROJECT_ROOT, ".env")
            dest_env = os.path.join(input_dir, ".env")
            if os.path.isfile(global_env):
                shutil.copy2(global_env, dest_env)
            else:
                with open(dest_env, "w", encoding="utf-8") as f:
                    f.write("PROVIDER_EXTRACTION=ollama\nEXTRACTOR_MODEL=llama3.2:3b\nPROVIDER_SYNTHESIS=ollama\nSYNTHESIZER_MODEL=llama3.2:3b\nJUDGE_PROVIDER=ollama\nJUDGE_MODEL=llama3.2:3b\nTEMPERATURE=0.2\nMAX_RETRIES=3\nMAX_PARALLEL_EXTRACTIONS=1\nLLM_TIMEOUT=300\n")

        global_config_dir = os.path.join(PROJECT_ROOT, "config")
        criteria_source = os.path.join(
            source_input_dir or global_config_dir, "criteria.yaml"
        )
        if not os.path.isfile(criteria_source):
            criteria_source = os.path.join(global_config_dir, "criteria.yaml")
        if not os.path.isfile(criteria_source):
            if os.path.isdir(run_dir):
                shutil.rmtree(run_dir, ignore_errors=True)
            return {"success": False, "error": "Global criteria.yaml is missing.", "status_code": 500}

        shutil.copy2(criteria_source, os.path.join(input_dir, "criteria.yaml"))
        for prompt_name in (
            "extractor_system.txt",
            "extractor_user.txt",
            "extractor_criterion.txt",
            "synthesizer_system.txt",
            "synthesizer_user.txt",
        ):
            source = os.path.join(
                source_input_dir or global_config_dir, "prompts", prompt_name
            )
            if not os.path.isfile(source):
                source = os.path.join(global_config_dir, "prompts", prompt_name)
            if os.path.isfile(source):
                shutil.copy2(source, os.path.join(prompts_dir, prompt_name))

        destination = os.path.join(papers_dir, safe_name)
        with open(destination, "wb") as output_file:
            output_file.write(content)

        extracted_title = None
        if extension == ".pdf":
            try:
                import fitz
                doc = fitz.open(destination)
                doc_title = (doc.metadata.get("title") or "").strip()
                from core.paper_ingestor import is_front_matter_line, extract_title_from_text
                if doc_title and len(doc_title) > 3 and not is_front_matter_line(doc_title):
                    extracted_title = doc_title
                else:
                    import pymupdf4llm
                    md0 = pymupdf4llm.to_markdown(doc, pages=[0])
                    cand = extract_title_from_text(md0)
                    if cand and cand != "No Title Found":
                        extracted_title = cand
            except Exception:
                pass

        metadata = {
            "original_filename": original_name,
            "safe_filename": safe_name,
            "file_size_bytes": len(content),
            "file_hash_sha256": content_hash,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "paper_title_extracted": extracted_title,
        }
        with open(os.path.join(input_dir, "run_metadata.json"), "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)

        return {
            "success": True,
            "is_duplicate": False,
            "content_hash": content_hash,
            "content_len": len(content),
            "original_filename": original_name,
            "safe_filename": safe_name,
            "paper_title": extracted_title,
            "run_dir": run_name,
            "status": "uploaded",
            "message": "Paper successfully uploaded and prepared for review.",
        }
    except Exception as e:
        if os.path.isdir(run_dir):
            shutil.rmtree(run_dir, ignore_errors=True)
        return {"success": False, "error": str(e)}


@app.post("/api/upload")
async def upload_paper(
    file: Optional[UploadFile] = File(None),
    files: Optional[List[UploadFile]] = File(None),
    base_run_dir: str = Form(""),
    force_new: bool = Form(False),
    duplicate_policy: str = Form("skip"),
    is_batch: bool = Form(False),
):
    """Store uploaded manuscripts in isolated run directories, supporting multi-file batch queuing."""
    uploaded_files: List[UploadFile] = []
    if files:
        uploaded_files.extend(files)
    if file:
        if not any(f.filename == file.filename and f is file for f in uploaded_files):
            uploaded_files.append(file)

    if not uploaded_files:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    # Single-file non-batch mode (preserves exact legacy contract and tests)
    if file is not None and not files and not is_batch:
        single_file = uploaded_files[0]
        original_name = os.path.basename(single_file.filename or "")
        content = await single_file.read()
        stage_res = _stage_uploaded_paper(content, original_name, base_run_dir, force_new, duplicate_policy)
        if not stage_res["success"]:
            status_code = stage_res.get("status_code", 400)
            if "20 MB" in stage_res.get("error", ""):
                status_code = 413
            raise HTTPException(status_code=status_code, detail=stage_res["error"])
        if stage_res.get("is_duplicate"):
            return {
                "run_dir": stage_res["run_dir"],
                "filename": stage_res["safe_filename"],
                "original_filename": stage_res["original_filename"],
                "paper_title": stage_res["paper_title"],
                "upload_date_str": stage_res["upload_date_str"],
                "run_label": stage_res["run_label"],
                "final_system_status": stage_res["final_system_status"],
                "overall_score": stage_res["overall_score"],
                "status": "duplicate",
                "message": "This exact paper already exists in the selected run.",
            }
        return {
            "run_dir": stage_res["run_dir"],
            "filename": stage_res["safe_filename"],
            "original_filename": stage_res["original_filename"],
            "paper_title": stage_res["paper_title"],
            "status": "uploaded",
            "message": "Paper successfully uploaded and prepared for review.",
        }

    # Multi-file or batch mode
    staged_items = []
    with _batch_lock:
        queue = _load_batch_queue()
        queue["duplicate_policy"] = duplicate_policy
        for ufile in uploaded_files:
            original_name = os.path.basename(ufile.filename or "")
            content = await ufile.read()
            stage_res = _stage_uploaded_paper(content, original_name, base_run_dir, force_new, duplicate_policy)
            next_pos = len(queue.get("items", [])) + 1
            item_id = f"batch_item_{int(time.time())}_{uuid.uuid4().hex[:6]}"

            if not stage_res["success"]:
                item = {
                    "item_id": item_id,
                    "position": next_pos,
                    "original_filename": original_name,
                    "safe_filename": original_name,
                    "paper_title": original_name,
                    "sha256": "",
                    "file_size_bytes": len(content) if content else 0,
                    "run_dir": "",
                    "status": "failed",
                    "stage": "Validation Failed",
                    "progress_pct": 0,
                    "error_message": stage_res["error"],
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "started_at": None,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "overall_score": None,
                    "provisional_recommendation": None,
                    "final_system_status": "Failed",
                }
            elif stage_res.get("is_duplicate"):
                item = {
                    "item_id": item_id,
                    "position": next_pos,
                    "original_filename": original_name,
                    "safe_filename": stage_res["safe_filename"],
                    "paper_title": stage_res["paper_title"],
                    "sha256": stage_res["content_hash"],
                    "file_size_bytes": stage_res["content_len"],
                    "run_dir": stage_res["run_dir"],
                    "status": "duplicate_skipped",
                    "stage": "Skipped (Duplicate)",
                    "progress_pct": 100,
                    "error_message": stage_res["message"],
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "started_at": None,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "overall_score": stage_res.get("overall_score"),
                    "provisional_recommendation": stage_res.get("recommendation"),
                    "final_system_status": stage_res.get("final_system_status"),
                }
            else:
                item = {
                    "item_id": item_id,
                    "position": next_pos,
                    "original_filename": original_name,
                    "safe_filename": stage_res["safe_filename"],
                    "paper_title": stage_res["paper_title"],
                    "sha256": stage_res["content_hash"],
                    "file_size_bytes": stage_res["content_len"],
                    "run_dir": stage_res["run_dir"],
                    "status": "queued",
                    "stage": "Queued",
                    "progress_pct": 0,
                    "error_message": None,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "started_at": None,
                    "finished_at": None,
                    "overall_score": None,
                    "provisional_recommendation": None,
                    "final_system_status": None,
                }
            queue["items"].append(item)
            staged_items.append(item)
        _save_batch_queue(queue)

    return {
        "status": "batch_staged",
        "total_files": len(uploaded_files),
        "queued_count": sum(1 for it in staged_items if it["status"] == "queued"),
        "skipped_count": sum(1 for it in staged_items if it["status"] == "duplicate_skipped"),
        "failed_count": sum(1 for it in staged_items if it["status"] == "failed"),
        "items": staged_items,
        "message": f"Successfully staged {len(staged_items)} files into the batch queue.",
    }


@app.post("/api/start")
async def start_run(request: Request):
    body = await request.json()
    run_dir = body.get("run_dir")
    mode = body.get("mode", "standard")
    config_overrides = body.get("config_overrides", {})

    if not run_dir:
        raise HTTPException(status_code=400, detail="run_dir is required")

    run_dir = _resolve_run_path(run_dir)

    # Prevent concurrent runs on the same directory
    for rid, info in _active_runs.items():
        if info["run_dir"] == run_dir:
            raise HTTPException(status_code=409, detail=f"A run is already active on this directory (run_id: {rid})")

    run_id = f"{os.path.basename(run_dir)}_{int(time.time())}"

    # Ensure SSE backend is active
    emitter = get_emitter()
    sse = get_sse_backend()
    if not sse:
        sse = SSEBackend()
        emitter.add_backend(sse)

    _active_runs[run_id] = {
        "run_dir": run_dir,
        "mode": mode,
        "started_at": time.time(),
        "status": "running",
    }

    thread = threading.Thread(
        target=_run_pipeline,
        args=(run_dir, run_id, mode, config_overrides),
        daemon=True,
    )
    thread.start()

    return {"run_id": run_id, "status": "started"}


@app.get("/api/events/{run_id}")
async def event_stream(run_id: str):
    """SSE endpoint for real-time progress."""
    async def event_generator():
        import asyncio as aio
        queue = asyncio.Queue()
        sse = get_sse_backend()
        if not sse:
            sse = SSEBackend()
            get_emitter().add_backend(sse)

        sse.add_listener(queue)
        try:
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30)
                    yield {"event": "progress", "data": data}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}
        finally:
            sse.remove_listener(queue)

    return EventSourceResponse(event_generator())


@app.get("/api/status/{run_id}")
async def get_status(run_id: str):
    if run_id in _active_runs:
        return {"status": "running", **_active_runs[run_id]}
    return {"status": "completed"}


@app.post("/api/stop/{run_id}")
async def stop_run(run_id: str):
    if run_id not in _active_runs:
        raise HTTPException(status_code=404, detail="Run not found")
    _cancel_flags[run_id] = True
    return {"status": "cancelling"}


# ---------------------------------------------------------------------------
# Batch processing — sequential run of all (or selected) run directories
# ---------------------------------------------------------------------------

def _run_batch_queue(mode: str = "standard"):
    """Sequential runner for queued batch items ensuring strict fault isolation."""
    global _batch_runner_thread
    with _batch_lock:
        if _batch_state.get("active"):
            return
        _batch_state["active"] = True
        _batch_state["cancelled"] = False
        _batch_state["started_at"] = time.time()

    emitter = get_emitter()
    sse = get_sse_backend()
    if not sse:
        sse = SSEBackend()
        emitter.add_backend(sse)

    try:
        while True:
            if _batch_state.get("cancelled"):
                break

            target_item = None
            with _batch_lock:
                queue = _load_batch_queue()
                for item in queue.get("items", []):
                    if item.get("status") == "queued":
                        target_item = item
                        break

                if not target_item:
                    break

                target_item["status"] = "running"
                target_item["stage"] = "Starting review pipeline"
                target_item["progress_pct"] = 10
                target_item["started_at"] = datetime.now(timezone.utc).isoformat()
                target_item["error_message"] = None
                _save_batch_queue(queue)

            _broadcast_batch_queue_event("batch_item_started", {"item": target_item})

            run_dir_name = target_item["run_dir"]
            full_run_dir = os.path.join(PROJECT_ROOT, run_dir_name)
            run_id = f"batch_{target_item['item_id']}_{int(time.time())}"

            _active_runs[run_id] = {
                "run_dir": full_run_dir,
                "mode": mode,
                "started_at": time.time(),
                "status": "running",
                "batch_item_id": target_item["item_id"],
            }

            try:
                _run_pipeline(full_run_dir, run_id, mode, {})

                csv_rows = _parse_csv_results(full_run_dir)
                score = None
                rec = None
                final_status = None
                if csv_rows:
                    row = csv_rows[0]
                    score_val = row.get("overall_score")
                    if score_val != "" and score_val is not None:
                        try:
                            score = float(score_val)
                        except (ValueError, TypeError):
                            pass
                    rec = row.get("recommendation")
                    final_status = row.get("final_system_status") or rec

                with _batch_lock:
                    queue = _load_batch_queue()
                    for item in queue.get("items", []):
                        if item.get("item_id") == target_item["item_id"]:
                            item["status"] = "completed"
                            item["stage"] = "Completed"
                            item["progress_pct"] = 100
                            item["finished_at"] = datetime.now(timezone.utc).isoformat()
                            if score is not None:
                                item["overall_score"] = score
                            if rec:
                                item["provisional_recommendation"] = rec
                            if final_status:
                                item["final_system_status"] = final_status
                            target_item = item
                            break
                    _save_batch_queue(queue)

                _broadcast_batch_queue_event("batch_item_completed", {"item": target_item})

            except Exception as e:
                err_text = str(e)
                with _batch_lock:
                    queue = _load_batch_queue()
                    for item in queue.get("items", []):
                        if item.get("item_id") == target_item["item_id"]:
                            item["status"] = "failed"
                            item["stage"] = "Failed"
                            item["error_message"] = err_text
                            item["finished_at"] = datetime.now(timezone.utc).isoformat()
                            target_item = item
                            break
                    _save_batch_queue(queue)

                _broadcast_batch_queue_event("batch_item_failed", {"item": target_item})
                emitter.emit(Error(stage_name="batch", message=f"Failed on item {target_item['original_filename']}: {err_text}", recoverable=True))

            finally:
                _active_runs.pop(run_id, None)

    finally:
        with _batch_lock:
            _batch_state["active"] = False
            _batch_state["cancelled"] = False
            final_queue = _load_batch_queue()
        _broadcast_batch_queue_event("batch_queue_finished", {
            "total": len(final_queue.get("items", [])),
            "completed": sum(1 for it in final_queue.get("items", []) if it.get("status") == "completed"),
            "failed": sum(1 for it in final_queue.get("items", []) if it.get("status") == "failed"),
            "interrupted": sum(1 for it in final_queue.get("items", []) if it.get("status") == "interrupted"),
            "skipped": sum(1 for it in final_queue.get("items", []) if it.get("status") == "duplicate_skipped"),
        })


def _run_batch(dirs: List[str], mode: str):
    """Legacy compatibility wrapper that stages dirs and executes the batch queue."""
    with _batch_lock:
        queue = _load_batch_queue()
        curr_len = len(queue.get("items", []))
        for i, d in enumerate(dirs, start=curr_len + 1):
            d_name = os.path.basename(d)
            queue["items"].append({
                "item_id": f"batch_item_{int(time.time())}_{uuid.uuid4().hex[:6]}",
                "position": i,
                "original_filename": d_name,
                "safe_filename": d_name,
                "paper_title": d_name,
                "sha256": "",
                "file_size_bytes": 0,
                "run_dir": d_name,
                "status": "queued",
                "stage": "Queued",
                "progress_pct": 0,
                "error_message": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "started_at": None,
                "finished_at": None,
                "overall_score": None,
                "provisional_recommendation": None,
                "final_system_status": None,
            })
        _save_batch_queue(queue)
    _run_batch_queue(mode)


@app.get("/api/batch-queue")
async def get_batch_queue():
    """Retrieve the current persisted batch queue state and execution progress."""
    with _batch_lock:
        queue = _load_batch_queue()
        items = queue.get("items", [])
        counts = {
            "queued": sum(1 for it in items if it.get("status") == "queued"),
            "running": sum(1 for it in items if it.get("status") == "running"),
            "completed": sum(1 for it in items if it.get("status") == "completed"),
            "failed": sum(1 for it in items if it.get("status") == "failed"),
            "interrupted": sum(1 for it in items if it.get("status") == "interrupted"),
            "duplicate_skipped": sum(1 for it in items if it.get("status") == "duplicate_skipped"),
        }
        return {
            "active": _batch_state.get("active", False),
            "total": len(items),
            "counts": counts,
            "items": items,
            "duplicate_policy": queue.get("duplicate_policy", "skip"),
            "updated_at": queue.get("updated_at"),
        }


@app.post("/api/batch-start")
@app.post("/api/batch/start")
async def batch_start(request: Request):
    """Start or resume processing queued batch items."""
    if _batch_state.get("active"):
        raise HTTPException(status_code=409, detail="A batch is already running")
    if _active_runs:
        raise HTTPException(status_code=409, detail="A single run is already active — stop it first")

    mode = "standard"
    selected_dirs = None
    try:
        body = await request.json()
        mode = body.get("mode", "standard")
        selected_dirs = body.get("dirs")
    except Exception:
        pass

    with _batch_lock:
        queue = _load_batch_queue()
        has_queued = any(it.get("status") == "queued" for it in queue.get("items", []))
        if not has_queued:
            all_dirs = _find_run_dirs(include_deleted=False)
            if selected_dirs:
                target_dirs = [d for d in all_dirs if d["name"] in selected_dirs]
            else:
                target_dirs = all_dirs

            if not target_dirs:
                raise HTTPException(status_code=400, detail="No queued items or run directories found")

            curr_len = len(queue.get("items", []))
            for i, r in enumerate(target_dirs, start=curr_len + 1):
                item_id = f"batch_item_{int(time.time())}_{uuid.uuid4().hex[:6]}"
                queue["items"].append({
                    "item_id": item_id,
                    "position": i,
                    "original_filename": r.get("original_filename", r["name"]),
                    "safe_filename": r.get("original_filename", r["name"]),
                    "paper_title": r.get("paper_title"),
                    "sha256": "",
                    "file_size_bytes": 0,
                    "run_dir": r["name"],
                    "status": "queued",
                    "stage": "Queued",
                    "progress_pct": 0,
                    "error_message": None,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "started_at": None,
                    "finished_at": None,
                    "overall_score": r.get("overall_score"),
                    "provisional_recommendation": r.get("provisional_recommendation"),
                    "final_system_status": r.get("final_system_status"),
                })
            _save_batch_queue(queue)

    thread = threading.Thread(target=_run_batch_queue, args=(mode,), daemon=True)
    thread.start()

    return {
        "status": "started",
        "active": True,
        "dirs": [it["run_dir"] for it in queue.get("items", []) if it.get("status") == "queued"],
        "total": len(queue.get("items", [])),
    }


@app.post("/api/batch-stop")
@app.post("/api/batch/stop")
async def batch_stop():
    """Cancel active batch queue processing."""
    with _batch_lock:
        if not _batch_state.get("active"):
            raise HTTPException(status_code=404, detail="No batch running")
        _batch_state["cancelled"] = True
        for rid in list(_cancel_flags.keys()):
            _cancel_flags[rid] = True
    return {"status": "cancelling", "message": "Batch cancellation requested"}


@app.post("/api/batch/retry-failed")
async def batch_retry_failed(request: Request = None):
    """Requeue only failed or interrupted items without rerunning completed ones."""
    retried_count = 0
    with _batch_lock:
        queue = _load_batch_queue()
        for item in queue.get("items", []):
            if item.get("status") in {"failed", "interrupted"}:
                item["status"] = "queued"
                item["stage"] = "Queued for retry"
                item["progress_pct"] = 0
                item["error_message"] = None
                item["started_at"] = None
                item["finished_at"] = None
                retried_count += 1
        if retried_count > 0:
            _save_batch_queue(queue)

    if retried_count > 0 and not _batch_state.get("active"):
        thread = threading.Thread(target=_run_batch_queue, args=("standard",), daemon=True)
        thread.start()

    return {
        "status": "retrying" if retried_count > 0 else "idle",
        "retried_count": retried_count,
        "message": f"Re-queued {retried_count} failed or interrupted items.",
        "queue": _load_batch_queue(),
    }


@app.post("/api/batch/items/{item_id}/retry")
async def batch_retry_single_item(item_id: str):
    """Requeue a specific failed or interrupted batch item."""
    found = False
    with _batch_lock:
        queue = _load_batch_queue()
        for item in queue.get("items", []):
            if item.get("item_id") == item_id:
                if item.get("status") == "running":
                    raise HTTPException(status_code=409, detail="Item is currently running")
                item["status"] = "queued"
                item["stage"] = "Queued for retry"
                item["progress_pct"] = 0
                item["error_message"] = None
                item["started_at"] = None
                item["finished_at"] = None
                found = True
                break
        if not found:
            raise HTTPException(status_code=404, detail=f"Batch item '{item_id}' not found")
        _save_batch_queue(queue)

    if not _batch_state.get("active"):
        thread = threading.Thread(target=_run_batch_queue, args=("standard",), daemon=True)
        thread.start()

    return {"status": "retrying", "item_id": item_id, "message": f"Item {item_id} re-queued for execution"}


@app.post("/api/batch/clear")
async def batch_clear():
    """Clear completed and duplicate-skipped items from the batch queue."""
    with _batch_lock:
        if _batch_state.get("active"):
            raise HTTPException(status_code=409, detail="Cannot clear queue while a batch is actively running")
        queue = _load_batch_queue()
        remaining = [it for it in queue.get("items", []) if it.get("status") not in {"completed", "duplicate_skipped"}]
        for i, it in enumerate(remaining, start=1):
            it["position"] = i
        queue["items"] = remaining
        _save_batch_queue(queue)
    return {"status": "cleared", "remaining_count": len(remaining), "queue": queue}


@app.get("/api/batch-status")
async def batch_status():
    """Legacy compatibility status endpoint reporting batch progress."""
    queue = _load_batch_queue()
    items = queue.get("items", [])
    completed = [it["run_dir"] for it in items if it.get("status") == "completed"]
    failed = [{"dir": it["run_dir"], "error": it.get("error_message")} for it in items if it.get("status") == "failed"]
    running = [it["run_dir"] for it in items if it.get("status") == "running"]
    return {
        "active": _batch_state.get("active", False),
        "dirs": [it["run_dir"] for it in items],
        "current_index": len(completed),
        "completed": completed,
        "failed": failed,
        "running": running,
    }


@app.get("/api/config/{run_dir:path}")
async def get_config(run_dir: str):
    full_path = _resolve_run_path(run_dir)
    return {"config": _load_config_safe(full_path)}


@app.get("/api/results/{run_dir:path}")
async def get_results(run_dir: str):
    full_path = _resolve_run_path(run_dir)
    return {"results": _parse_csv_results(full_path)}


def _parse_review_metadata_from_text(content: str, fname: str) -> Dict[str, Any]:
    """Parse structured metadata from markdown review content."""
    import re
    parts = fname.replace(".md", "").split("_")
    timestamp = ""
    extractor = ""
    synthesizer = ""
    paper = ""

    # Find timestamp pattern (8 digits _ 6 digits)
    ts_idx = None
    for i, p in enumerate(parts):
        if re.match(r"^\d{8}$", p) and i + 1 < len(parts) and re.match(r"^\d{6}$", parts[i + 1]):
            ts_idx = i
            timestamp = f"{p}_{parts[i+1]}"
            break

    if ts_idx is not None:
        paper = "_".join(parts[:ts_idx - 4]) if ts_idx >= 5 else "_".join(parts[:ts_idx])
        extractor = "/".join(parts[ts_idx - 4:ts_idx - 2]) if ts_idx >= 4 else ""
        synthesizer = "/".join(parts[ts_idx - 2:ts_idx]) if ts_idx >= 2 else ""
    else:
        paper = fname.replace(".md", "")

    score = None
    recommendation = None
    cost = None
    confidence = None
    review_status = "legacy"
    successful_criteria = None
    total_criteria = None
    verified_evidence_count = None
    unverified_evidence_count = None
    provisional_recommendation = None

    try:
        head = content[:4000]
        tail = content[-1500:] if len(content) > 1500 else content

        metadata_head = re.sub(r"\*{1,2}", "", head)
        metadata_tail = re.sub(r"\*{1,2}", "", tail)

        m = re.search(r"Review Status:\s*([a-z_]+)", metadata_head, re.IGNORECASE)
        if m:
            review_status = m.group(1).lower()

        m = re.search(r"Overall Score:.*?(\d+\.?\d*)\s*(?:/\s*100)?", metadata_head, re.IGNORECASE)
        if m:
            score = float(m.group(1))

        if review_status == "failed" or "none (analysis failed)" in metadata_head.lower():
            score = None

        m = re.search(r"Criteria:\s*([0-9]+)\s*/\s*([0-9]+)\s+successful", metadata_head, re.IGNORECASE)
        if m:
            successful_criteria, total_criteria = int(m.group(1)), int(m.group(2))

        m = re.search(r"Verified Evidence:\s*([0-9]+)", metadata_head, re.IGNORECASE)
        if m:
            verified_evidence_count = int(m.group(1))

        m = re.search(r"Unverified Evidence:\s*([0-9]+)", metadata_head, re.IGNORECASE)
        if m:
            unverified_evidence_count = int(m.group(1))

        m = re.search(r"Provisional Recommendation:\s*([^\r\n]+)", metadata_head, re.IGNORECASE)
        if m:
            provisional_recommendation = m.group(1).strip()

        m = re.search(r"Final System Recommendation:\s*([^\r\n]+)", metadata_head, re.IGNORECASE)
        if m:
            recommendation = m.group(1).strip()
        else:
            m = re.search(r"Recommendation:\s*([^\r\n]+)", metadata_head, re.IGNORECASE)
            if m:
                recommendation = m.group(1).strip()

        m = re.search(r"(?:Total\s+)?(?:API\s+)?Cost:.*?[\$]?([\d.]+)", metadata_tail, re.IGNORECASE)
        if m:
            cost = float(m.group(1))

        m = re.search(r"(?:Heuristic Reliability Estimate|Confidence):\s*(\d+\.?\d*)\s*%?", metadata_head, re.IGNORECASE)
        if m:
            val = float(m.group(1))
            confidence = val if val <= 1.0 else val / 100.0
    except Exception:
        pass

    display_date = ""
    if timestamp:
        try:
            display_date = datetime.strptime(timestamp, "%Y%m%d_%H%M%S").strftime("%Y-%m-%d %H:%M")
        except ValueError:
            display_date = timestamp

    return {
        "filename": fname,
        "paper": paper,
        "extractor_model": extractor,
        "synthesizer_model": synthesizer,
        "timestamp": timestamp,
        "display_date": display_date,
        "overall_score": score,
        "recommendation": recommendation,
        "provisional_recommendation": provisional_recommendation,
        "review_status": review_status,
        "successful_criteria": successful_criteria,
        "total_criteria": total_criteria,
        "verified_evidence_count": verified_evidence_count,
        "unverified_evidence_count": unverified_evidence_count,
        "cost": cost,
        "total_cost": cost,
        "confidence": confidence,
    }


@app.get("/api/all-reviews/{run_dir:path}")
async def get_all_reviews(run_dir: str):
    """Return ALL review files for a run directory with parsed metadata and scores."""
    full_path = _resolve_run_path(run_dir)
    reviews_dir = os.path.join(full_path, "outputs", "reviews")
    if not os.path.isdir(reviews_dir):
        return {"reviews": []}

    reviews = []
    for f in sorted(glob.glob(os.path.join(reviews_dir, "*.md")), reverse=True):
        fname = os.path.basename(f)
        try:
            with open(f, encoding="utf-8") as fh:
                content = fh.read()
            review_meta = _parse_review_metadata_from_text(content, fname)
            review_meta["size"] = os.path.getsize(f)
            reviews.append(review_meta)
        except Exception:
            reviews.append({
                "filename": fname,
                "paper": fname.replace(".md", ""),
                "size": os.path.getsize(f) if os.path.exists(f) else 0,
            })

    return {"reviews": reviews}


def _run_overview_generation(run_dir: str, full_path: str, actual_file_hash: str, force: bool = False):
    """Worker task executing in independent background thread to generate and cache paper overview."""
    with _overview_lock:
        _overview_jobs[run_dir] = {
            "status": "running",
            "stage": "Reading manuscript and extracted content",
            "progress": 15,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "error": None,
            "overview": None,
        }

    try:
        paper_obj = None
        cache_path = os.path.join(full_path, "ingestion_cache.json")
        if os.path.isfile(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    cdata = json.load(f)
                if isinstance(cdata, list) and cdata:
                    from core.data_models import Paper
                    paper_obj = Paper.model_validate(cdata[0])
            except Exception:
                paper_obj = None

        if paper_obj is None:
            papers_dir = os.path.join(full_path, "papers")
            paper_files = [f for f in os.listdir(papers_dir) if not f.startswith(".")] if os.path.isdir(papers_dir) else []
            if not paper_files:
                raise FileNotFoundError(f"No manuscript file found in {papers_dir}")
            pdf_path = os.path.join(papers_dir, paper_files[0])
            from core.paper_ingestor import ingest_paper
            paper_obj = ingest_paper(pdf_path, show_progress=False)
            if paper_obj is None:
                raise ValueError("Failed to ingest manuscript content from file")

        with _overview_lock:
            if run_dir in _overview_jobs:
                _overview_jobs[run_dir]["stage"] = "Analyzing manuscript across 8 dimensions"
                _overview_jobs[run_dir]["progress"] = 45

        from core.config_loader import Config
        input_dir = os.path.join(full_path, "input") if os.path.isdir(os.path.join(full_path, "input")) else "config"
        cfg = Config(config_path=input_dir)

        overview = generate_paper_overview(paper_obj, run_dir, actual_file_hash, cfg)

        with _overview_lock:
            if run_dir in _overview_jobs:
                _overview_jobs[run_dir]["stage"] = "Validating overview contract"
                _overview_jobs[run_dir]["progress"] = 85

        valid, errors = validate_overview_payload(overview.model_dump())
        if not valid:
            raise ValueError(f"Overview contract validation failed: {'; '.join(errors)}")

        save_paper_overview_cache(full_path, overview)

        with _overview_lock:
            _overview_jobs[run_dir] = {
                "status": "completed",
                "stage": "Completed",
                "progress": 100,
                "started_at": _overview_jobs.get(run_dir, {}).get("started_at"),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error": None,
                "overview": overview.model_dump(),
            }

    except Exception as e:
        with _overview_lock:
            _overview_jobs[run_dir] = {
                "status": "failed",
                "stage": "Failed",
                "progress": 0,
                "started_at": _overview_jobs.get(run_dir, {}).get("started_at"),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error": str(e),
                "overview": None,
            }


@app.get("/api/overview/{run_dir:path}")
async def get_paper_overview(run_dir: str):
    """Read-only endpoint to inspect paper overview for a run. Never triggers generation or writes files."""
    full_path = _resolve_run_path(run_dir, allow_deleted=False)

    papers_dir = os.path.join(full_path, "papers")
    actual_hash = None
    if os.path.isdir(papers_dir):
        files = [f for f in os.listdir(papers_dir) if not f.startswith(".")]
        if files:
            actual_hash = compute_file_sha256(os.path.join(papers_dir, files[0]))

    cached_data, cache_status = load_paper_overview_cache(full_path, expected_sha256=actual_hash)
    if cache_status == "valid" and cached_data is not None:
        return {
            "status": "cached",
            "run_dir": run_dir,
            "overview": cached_data.model_dump(),
        }
    if cache_status == "stale":
        return {
            "status": "stale",
            "run_dir": run_dir,
            "message": "Manuscript on disk has changed. Cache is stale; please generate a fresh overview.",
        }

    with _overview_lock:
        job = _overview_jobs.get(run_dir)

    if job:
        if job["status"] in ("queued", "running"):
            return {
                "status": job["status"],
                "run_dir": run_dir,
                "stage": job.get("stage", "Processing"),
                "progress": job.get("progress", 0),
            }
        if job["status"] == "failed":
            return {
                "status": "failed",
                "run_dir": run_dir,
                "stage": "Failed",
                "error": job.get("error", "Unknown generation error"),
            }
        if job["status"] == "completed" and job.get("overview"):
            return {
                "status": "cached",
                "run_dir": run_dir,
                "overview": job["overview"],
            }

    return {
        "status": "not_generated",
        "run_dir": run_dir,
        "message": "Paper overview has not been generated for this run yet.",
    }


@app.post("/api/overview/{run_dir:path}/generate")
async def generate_overview_endpoint(run_dir: str, force: bool = False):
    """Explicit endpoint to generate or regenerate paper overview asynchronously."""
    full_path = _resolve_run_path(run_dir, allow_deleted=False)

    papers_dir = os.path.join(full_path, "papers")
    if not os.path.isdir(papers_dir):
        raise HTTPException(status_code=400, detail="Run directory contains no papers folder")
    files = [f for f in os.listdir(papers_dir) if not f.startswith(".")]
    if not files:
        raise HTTPException(status_code=400, detail="No manuscript file found in run directory")
    actual_hash = compute_file_sha256(os.path.join(papers_dir, files[0]))

    if not force:
        cached_data, cache_status = load_paper_overview_cache(full_path, expected_sha256=actual_hash)
        if cache_status == "valid" and cached_data is not None:
            return JSONResponse(
                status_code=200,
                content={
                    "status": "cached",
                    "run_dir": run_dir,
                    "message": "Valid overview cache already exists",
                    "overview": cached_data.model_dump(),
                }
            )

    with _overview_lock:
        current_job = _overview_jobs.get(run_dir)
        if current_job and current_job.get("status") in ("queued", "running"):
            return JSONResponse(
                status_code=200,
                content={
                    "status": "running",
                    "run_dir": run_dir,
                    "stage": current_job.get("stage", "In progress"),
                    "progress": current_job.get("progress", 0),
                    "message": "Overview generation is already running for this run",
                }
            )

        _overview_jobs[run_dir] = {
            "status": "queued",
            "stage": "Queued",
            "progress": 0,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "error": None,
            "overview": None,
        }

    thread = threading.Thread(
        target=_run_overview_generation,
        args=(run_dir, full_path, actual_hash, force),
        daemon=True,
    )
    thread.start()

    return JSONResponse(
        status_code=202,
        content={
            "status": "queued",
            "run_dir": run_dir,
            "message": "Paper overview generation initiated",
        }
    )


@app.get("/api/review/{run_dir:path}/{filename:path}")
async def get_review(run_dir: str, filename: str):
    full_path = _resolve_run_path(run_dir)
    reviews_dir = Path(full_path) / "outputs" / "reviews"
    review_path = (reviews_dir / filename).resolve()
    if not str(review_path).startswith(str(reviews_dir.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")
    if not review_path.exists():
        raise HTTPException(status_code=404, detail="Review not found")
    if filename.endswith(".pdf"):
        return FileResponse(str(review_path), media_type="application/pdf", filename=review_path.name)
    return FileResponse(str(review_path), media_type="text/markdown")


@app.get("/api/evidence/{run_dir:path}/{paper_filename}")
async def get_evidence(run_dir: str, paper_filename: str):
    """Return saved evidence records for a paper."""
    full_path = _resolve_run_path(run_dir)
    safe_name = Path(paper_filename).stem
    evidence_path = (
        Path(full_path) / "outputs" / "evidence" / f"{safe_name}_evidence.json"
    ).resolve()

    evidence_dir = (Path(full_path) / "outputs" / "evidence").resolve()
    if not str(evidence_path).startswith(str(evidence_dir) + os.sep):
        raise HTTPException(status_code=403, detail="Access denied")

    if not evidence_path.is_file():
        return {"paper_filename": paper_filename, "evidence": []}

    try:
        records = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise HTTPException(status_code=500, detail="Could not read evidence data")

    # Add section mappings to the response only; never rewrite saved evidence.
    cache_path = Path(full_path) / "ingestion_cache.json"
    if isinstance(records, list) and cache_path.is_file():
        try:
            cache_data = json.loads(cache_path.read_text(encoding="utf-8"))
            cached_paper = next(
                (
                    item for item in cache_data
                    if isinstance(item, dict)
                    and str(item.get("filename", "")).casefold() == paper_filename.casefold()
                    and isinstance(item.get("content_markdown"), str)
                    and item["content_markdown"].strip()
                ),
                None,
            ) if isinstance(cache_data, list) else None

            if cached_paper is not None:
                from agents.evidence_section_mapper import map_evidence_to_sections
                records = map_evidence_to_sections(
                    cached_paper["content_markdown"], records
                )
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            # Mapping is optional; preserve the original API behavior on failure.
            pass

    return {"paper_filename": paper_filename, "evidence": records}


@app.get("/api/section-analysis/{run_dir:path}/{paper_filename}")
async def get_section_analysis(run_dir: str, paper_filename: str, request: Request):
    """Return the saved academic section analysis for a paper."""
    import re
    from urllib.parse import unquote

    raw_path = request.scope.get("raw_path", b"").decode("latin-1").lower()
    decoded_path = unquote(raw_path)
    if (
        "%2f" in raw_path
        or "%5c" in raw_path
        or "/../" in decoded_path
        or "\\\\" in decoded_path
    ):
        raise HTTPException(status_code=400, detail="Invalid paper filename")

    full_path = _resolve_run_path(run_dir)
    safe_name = Path(paper_filename).name
    if (
        safe_name != paper_filename
        or safe_name in {".", ".."}
        or ".." in safe_name
        or "/" in paper_filename
        or "\\" in paper_filename
    ):
        raise HTTPException(status_code=400, detail="Invalid paper filename")

    report_name = re.sub(r"[^A-Za-z0-9._-]+", "_", safe_name)
    report_path = (
        Path(full_path) / "outputs" / "section_analysis" / f"{report_name}.json"
    ).resolve()
    report_dir = (Path(full_path) / "outputs" / "section_analysis").resolve()

    if not str(report_path).startswith(str(report_dir) + os.sep):
        raise HTTPException(status_code=403, detail="Access denied")
    if not report_path.is_file():
        return {"paper_filename": paper_filename, "sections": {}, "available": False}

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise HTTPException(status_code=500, detail="Could not read section analysis")

    return {**report, "available": True}


@app.get("/api/section-analysis-for-review/{run_dir:path}/{review_filename:path}")
async def get_section_analysis_for_review(
    run_dir: str, review_filename: str, request: Request
):
    """Find the original paper for a review and return its section analysis."""
    from urllib.parse import unquote

    raw_path = request.scope.get("raw_path", b"").decode("latin-1").lower()
    decoded_path = unquote(raw_path)
    if "%2f" in raw_path or "%5c" in raw_path or "/../" in decoded_path:
        raise HTTPException(status_code=400, detail="Invalid review filename")

    full_path = Path(_resolve_run_path(run_dir))
    safe_review_name = Path(review_filename).name
    if safe_review_name != review_filename or not safe_review_name.endswith(".md"):
        raise HTTPException(status_code=400, detail="Invalid review filename")

    papers_dir = full_path / "papers"
    if not papers_dir.is_dir():
        return {"available": False, "sections": {}}

    candidates = [
        paper for paper in papers_dir.iterdir()
        if paper.is_file()
        and paper.suffix.lower() in {".pdf", ".docx"}
        and safe_review_name.startswith(paper.stem + "_")
    ]
    if not candidates:
        return {"available": False, "sections": {}}

    original_paper = max(candidates, key=lambda item: len(item.stem))
    report_name = "".join(
        char if char.isalnum() or char in "._-" else "_"
        for char in original_paper.name
    )
    report_path = (
        full_path / "outputs" / "section_analysis" / f"{report_name}.json"
    ).resolve()
    report_dir = (full_path / "outputs" / "section_analysis").resolve()

    if not str(report_path).startswith(str(report_dir) + str(Path("/"))):
        raise HTTPException(status_code=403, detail="Access denied")
    if not report_path.is_file():
        return {
            "available": False,
            "paper_filename": original_paper.name,
            "sections": {},
        }

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise HTTPException(status_code=500, detail="Could not read section analysis")

    return {**report, "available": True, "paper_filename": original_paper.name}


@app.get("/api/quality-analysis-for-review/{run_dir:path}/{review_filename:path}")
async def get_quality_analysis_for_review(
    run_dir: str, review_filename: str, request: Request
):
    """Return supplemental heuristic reports associated with a saved review."""
    import re
    from urllib.parse import unquote

    raw_path = request.scope.get("raw_path", b"").decode("latin-1").lower()
    decoded_path = unquote(raw_path)
    if "%2f" in raw_path or "%5c" in raw_path or "/../" in decoded_path:
        raise HTTPException(status_code=400, detail="Invalid review filename")

    full_path = Path(_resolve_run_path(run_dir))
    safe_review_name = Path(review_filename).name
    if safe_review_name != review_filename or not safe_review_name.endswith(".md"):
        raise HTTPException(status_code=400, detail="Invalid review filename")

    papers_dir = full_path / "papers"
    if not papers_dir.is_dir():
        return {"available": False, "paper_filename": None, "analyses": {}}

    candidates = [
        paper for paper in papers_dir.iterdir()
        if paper.is_file()
        and paper.suffix.lower() in {".pdf", ".docx"}
        and safe_review_name.startswith(paper.stem + "_")
    ]
    if not candidates:
        return {"available": False, "paper_filename": None, "analyses": {}}

    original_paper = max(candidates, key=lambda item: len(item.stem))
    safe_name = re.sub(
        r"[^A-Za-z0-9._-]+", "_", Path(original_paper.name).name
    )
    report_dir = (full_path / "outputs" / "quality_analysis").resolve()
    expected_parent = (full_path / "outputs").resolve()
    if report_dir.parent != expected_parent:
        raise HTTPException(status_code=403, detail="Access denied")

    analyses = {}
    for name in (
        "citation_reference",
        "document_structure",
        "cross_section_consistency",
    ):
        report_path = report_dir / f"{safe_name}.{name}.json"
        if not report_path.is_file():
            analyses[name] = {"available": False, "status": "not_generated"}
            continue
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            analyses[name] = {"available": False, "status": "unreadable"}
            continue
        analyses[name] = {"available": True, **report}

    return {
        "available": any(item.get("available") for item in analyses.values()),
        "paper_filename": original_paper.name,
        "analyses": analyses,
    }


@app.get("/api/criteria/{run_dir:path}")
async def get_criteria(run_dir: str):
    full_path = _resolve_run_path(run_dir)
    return {"criteria": _load_criteria(full_path)}


@app.get("/api/criteria-raw/{run_dir:path}")
async def get_criteria_raw(run_dir: str):
    """Return raw YAML text of criteria.yaml for editing."""
    full_path = _resolve_run_path(run_dir)
    criteria_path = os.path.join(full_path, "input", "criteria.yaml")
    if not os.path.exists(criteria_path):
        criteria_path = os.path.join(PROJECT_ROOT, "config", "criteria.yaml")
    if not os.path.exists(criteria_path):
        raise HTTPException(status_code=404, detail="criteria.yaml not found")
    with open(criteria_path) as f:
        return {"content": f.read()}


@app.put("/api/criteria/{run_dir:path}")
async def update_criteria(run_dir: str, request: Request):
    """Update criteria.yaml for a run directory."""
    import yaml
    body = await request.json()
    content = body.get("content", "")
    try:
        yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")

    full_path = _resolve_run_path(run_dir)
    criteria_path = os.path.join(full_path, "input", "criteria.yaml")
    if not os.path.exists(criteria_path):
        os.makedirs(os.path.dirname(criteria_path), exist_ok=True)
    try:
        with open(criteria_path, "w") as f:
            f.write(content)
    except (IOError, OSError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to write criteria: {e}")
    return {"success": True}


@app.put("/api/config/{run_dir:path}")
async def update_config(run_dir: str, request: Request):
    """Update a single key in the run's .env file."""
    ALLOWED_KEYS = {
        "PROVIDER_EXTRACTION", "PROVIDER_SYNTHESIS",
        "EXTRACTOR_MODEL", "SYNTHESIZER_MODEL",
        "MODEL_EXTRACTION", "MODEL_SYNTHESIS",
        "TEMPERATURE", "TEMPERATURE_EXTRACTION", "TEMPERATURE_SYNTHESIS",
        "MAX_TOKENS_EXTRACTION", "MAX_TOKENS_SYNTHESIS", "MAX_TOKENS_JUDGE",
        "TOKEN_LIMIT_DEFAULT",
        "EXTRACTION_BATCH_SIZE", "MAX_PARALLEL_EXTRACTIONS", "CONCURRENCY",
        "DOMAIN", "LANGUAGE",
        "MAX_RETRIES", "JUDGE_PROVIDER", "JUDGE_MODEL", "JUDGE_TEMPERATURE",
        "LITERATURE_GROUNDING_ENABLED",
        "LIBRARIAN_TEMPERATURE", "LIBRARIAN_SUMMARY_TEMPERATURE",
        "FACT_CHECKER_TEMPERATURE", "CRITIC_TEMPERATURE",
        "CRITIC_MAX_JSON_RETRIES", "FACT_CHECKER_MAX_RETRIES",
        "LLM_TIMEOUT", "API_TIMEOUT",
    }
    body = await request.json()
    key = body.get("key", "")
    value = body.get("value", "")

    # Sanitize NaN/Inf from numeric inputs
    if isinstance(value, float) and (value != value or abs(value) == float("inf")):
        raise HTTPException(status_code=400, detail="Value must be a valid number")

    # Prevent newline injection
    if isinstance(value, str):
        value = value.replace("\n", "").replace("\r", "")

    if not key:
        raise HTTPException(status_code=400, detail="key is required")
    if "KEY" in key.upper() or "SECRET" in key.upper():
        raise HTTPException(status_code=400, detail="Cannot modify API keys via the dashboard")
    if key not in ALLOWED_KEYS:
        raise HTTPException(status_code=400, detail=f"Key '{key}' is not editable. Allowed: {sorted(ALLOWED_KEYS)}")

    full_path = _resolve_run_path(run_dir)
    env_path = os.path.join(full_path, "input", ".env")
    if not os.path.exists(env_path):
        raise HTTPException(status_code=404, detail=".env not found")

    # Read, update, write back — preserving comments and order
    lines = []
    found = False
    with open(env_path) as f:
        for line in f:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k, _ = stripped.split("=", 1)
                if k == key:
                    lines.append(f"{key}={value}\n")
                    found = True
                    continue
            lines.append(line)
    if not found:
        lines.append(f"{key}={value}\n")
    with open(env_path, "w") as f:
        f.writelines(lines)

    return {"config": _load_config_safe(full_path)}


@app.get("/api/prompts")
async def get_prompts():
    """Return all prompt files with their content."""
    prompts_dir = os.path.join(PROJECT_ROOT, "config", "prompts")
    if not os.path.isdir(prompts_dir):
        return {"prompts": []}
    prompts = []
    for fn in sorted(os.listdir(prompts_dir)):
        if fn.endswith(".txt"):
            with open(os.path.join(prompts_dir, fn)) as f:
                prompts.append({"filename": fn, "content": f.read()})
    return {"prompts": prompts}


@app.put("/api/prompts/{filename}")
async def update_prompt(filename: str, request: Request):
    """Update a prompt file."""
    ALLOWED_PROMPTS = {
        "extractor_system.txt", "extractor_user.txt",
        "synthesizer_system.txt", "synthesizer_user.txt",
    }
    if filename not in ALLOWED_PROMPTS:
        raise HTTPException(status_code=400, detail=f"Unknown prompt file: {filename}")

    body = await request.json()
    content = body.get("content", "")

    prompt_path = os.path.join(PROJECT_ROOT, "config", "prompts", filename)
    try:
        with open(prompt_path, "w") as f:
            f.write(content)
    except (IOError, OSError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to write prompt: {e}")
    return {"success": True}


@app.get("/api/literature-sources")
async def get_literature_sources():
    """Return literature_sources.yaml content."""
    path = os.path.join(PROJECT_ROOT, "config", "literature_sources.yaml")
    if not os.path.exists(path):
        return {"content": ""}
    with open(path) as f:
        return {"content": f.read()}


@app.put("/api/literature-sources")
async def update_literature_sources(request: Request):
    """Update literature_sources.yaml."""
    import yaml
    body = await request.json()
    content = body.get("content", "")
    try:
        yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")

    path = os.path.join(PROJECT_ROOT, "config", "literature_sources.yaml")
    try:
        with open(path, "w") as f:
            f.write(content)
    except (IOError, OSError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to write literature sources: {e}")
    return {"success": True}


@app.get("/api/model-costs")
async def get_model_costs():
    """Return model_costs.yaml content and litellm's known pricing for active models."""
    import yaml
    path = os.path.join(PROJECT_ROOT, "config", "model_costs.yaml")
    yaml_content = ""
    custom_models = {}
    if os.path.exists(path):
        with open(path) as f:
            yaml_content = f.read()
        try:
            data = yaml.safe_load(yaml_content) or {}
            custom_models = data.get("models", {})
        except yaml.YAMLError:
            pass

    # Also return litellm's built-in pricing for commonly used models
    import litellm
    builtin = {}
    for model_key in list(litellm.model_cost.keys()):
        info = litellm.model_cost[model_key]
        inp = info.get("input_cost_per_token", 0)
        out = info.get("output_cost_per_token", 0)
        if inp or out:
            builtin[model_key] = {
                "input_cost_per_million": round(inp * 1_000_000, 4),
                "output_cost_per_million": round(out * 1_000_000, 4),
                "source": "litellm" if model_key not in custom_models else "custom",
            }

    return {"content": yaml_content, "custom_models": custom_models, "builtin_count": len(builtin)}


@app.put("/api/model-costs")
async def update_model_costs(request: Request):
    """Update model_costs.yaml and re-register models with litellm."""
    import yaml
    body = await request.json()
    content = body.get("content", "")
    try:
        data = yaml.safe_load(content)
        if data and "models" in data:
            for name, info in data["models"].items():
                if not isinstance(info, dict):
                    raise ValueError(f"Model '{name}' must be a mapping")
        elif data is not None:
            raise ValueError("YAML must contain a 'models' key")
    except (yaml.YAMLError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")

    path = os.path.join(PROJECT_ROOT, "config", "model_costs.yaml")
    try:
        with open(path, "w") as f:
            f.write(content)
    except (IOError, OSError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to write model costs: {e}")

    # Re-register models with litellm
    from core.llm_wrapper import _load_and_register_custom_models
    _load_and_register_custom_models()

    return {"success": True}


@app.get("/api/model-costs/lookup/{model_name:path}")
async def lookup_model_cost(model_name: str):
    """Look up litellm's pricing for a specific model."""
    import litellm
    info = litellm.model_cost.get(model_name, {})
    if not info:
        raise HTTPException(status_code=404, detail=f"Model '{model_name}' not found in litellm registry")
    return {
        "model": model_name,
        "input_cost_per_million": round(info.get("input_cost_per_token", 0) * 1_000_000, 4),
        "output_cost_per_million": round(info.get("output_cost_per_token", 0) * 1_000_000, 4),
        "max_input_tokens": info.get("max_input_tokens"),
        "max_output_tokens": info.get("max_output_tokens"),
    }


@app.get("/api/reviews/{run_dir:path}")
async def list_reviews(run_dir: str):
    full_path = _resolve_run_path(run_dir)
    return {"reviews": _get_reviews(full_path)}


@app.get("/api/reports/csv")
async def download_archive_csv_report():
    """Download consolidated CSV report of all historical reviews in the archive."""
    import io
    all_runs = _find_run_dirs()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "run_directory",
        "paper_filename",
        "title",
        "upload_date",
        "extractor_model",
        "synthesizer_model",
        "overall_score",
        "recommendation",
        "status",
        "total_cost",
        "heuristic_reliability",
    ])
    for r in all_runs:
        score_val = ""
        if r.get("overall_score") is not None:
            score_val = f"{float(r['overall_score']):.1f}"
        writer.writerow([
            r.get("name", ""),
            r.get("original_filename", ""),
            r.get("paper_title") or r.get("original_filename") or "",
            r.get("upload_date_str", ""),
            "ollama/llama3.2:3b",
            "ollama/llama3.2:3b",
            score_val,
            r.get("final_system_status") or r.get("status_desc") or "",
            r.get("status_desc") or r.get("status") or "",
            "$0.00",
            "Heuristic (Local)",
        ])
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"review_archive_{timestamp}.csv"
    csv_bytes = output.getvalue().encode("utf-8")
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/api/reports/csv/{run_dir:path}")
async def download_csv_report(run_dir: str):
    """Download the latest consolidated CSV report for a run directory."""
    full_path = _resolve_run_path(run_dir)
    reports_dir = os.path.join(full_path, "outputs", "reports")
    if not os.path.isdir(reports_dir):
        raise HTTPException(status_code=404, detail="No reports directory found")
    csv_files = (
        sorted(glob.glob(os.path.join(reports_dir, "consolidated_reviews_*.csv")), reverse=True)
        or sorted(glob.glob(os.path.join(reports_dir, "report_consolidated_*.csv")), reverse=True)
    )
    if not csv_files:
        raise HTTPException(status_code=404, detail="No consolidated CSV report found")
    filename = os.path.basename(csv_files[0])
    return FileResponse(
        csv_files[0],
        media_type="text/csv; charset=utf-8",
        filename=filename,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/api/export/audit/{run_dir:path}/{filename:path}")
async def export_audit_json(run_dir: str, filename: str, request: Request):
    """Return a comprehensive research intelligence audit artifact for a paper review."""
    from urllib.parse import unquote
    raw_path = request.scope.get("raw_path", b"").decode("latin-1").lower()
    decoded_path = unquote(raw_path)
    if "%2f" in raw_path or "%5c" in raw_path or "/../" in decoded_path:
        raise HTTPException(status_code=400, detail="Invalid review filename")

    full_path = Path(_resolve_run_path(run_dir))
    safe_filename = Path(filename).name
    if safe_filename != filename or not safe_filename.endswith(".md"):
        raise HTTPException(status_code=400, detail="Invalid review filename")

    reviews_dir = (full_path / "outputs" / "reviews").resolve()
    review_path = (reviews_dir / filename).resolve()
    if not str(review_path).startswith(str(reviews_dir)):
        raise HTTPException(status_code=403, detail="Access denied")
    if not review_path.is_file():
        raise HTTPException(status_code=404, detail="Review not found")

    content = review_path.read_text(encoding="utf-8")
    meta = _parse_review_metadata_from_text(content, filename)

    # Load section analysis and quality analysis using existing endpoint logic
    sec_result = await get_section_analysis_for_review(run_dir, filename, request)
    quality_result = await get_quality_analysis_for_review(run_dir, filename, request)

    # Load evidence records
    paper_name = meta.get("paper") or ""
    paper_stem = filename.split("_20")[0] if "_20" in filename else filename.replace(".md", "")
    evidence_items = []
    ev_dir = full_path / "outputs" / "evidence"
    if ev_dir.is_dir():
        candidates = []
        if paper_name:
            candidates = list(ev_dir.glob(f"*{paper_name}*_evidence.json"))
        if not candidates:
            candidates = list(ev_dir.glob(f"*{paper_stem[:15]}*_evidence.json"))
        if candidates:
            try:
                evidence_items = json.loads(candidates[0].read_text(encoding="utf-8"))
            except Exception:
                evidence_items = []

    safe_config = _load_config_safe(str(full_path))

    audit_payload = {
        "system": "Research Intelligence Studio",
        "export_version": "1.0",
        "export_type": "full_research_audit",
        "generated_at": datetime.now().isoformat(),
        "run_directory": full_path.name,
        "review_filename": filename,
        "governance_invariants": {
            "invariant_1": "Strict separation: Diagnostic Heuristic Score vs Provisional Recommendation vs Final System Status",
            "invariant_5": "Quotation verification strictly confirms text presence in source manuscript; rubric relevance requires human peer review",
            "diagnostic_score": meta.get("overall_score"),
            "provisional_recommendation": meta.get("provisional_recommendation"),
            "final_system_status": meta.get("recommendation"),
            "review_status": meta.get("review_status"),
        },
        "metadata": meta,
        "evidence_grounding": {
            "total_records": len(evidence_items),
            "verified_count": meta.get("verified_evidence_count"),
            "unverified_count": meta.get("unverified_evidence_count"),
            "records": evidence_items,
        },
        "section_analysis": sec_result,
        "quality_analysis": quality_result,
        "active_configuration": safe_config,
        "raw_markdown": content,
    }
    return JSONResponse(
        content=audit_payload,
        headers={"Content-Disposition": f'attachment; filename="audit_{Path(filename).stem}.json"'},
    )


@app.get("/api/export/pdf/{run_dir:path}/{filename:path}")
async def export_review_pdf(run_dir: str, filename: str, request: Request):
    """Return or generate a comprehensive, publication-quality PDF report for a paper review."""
    from urllib.parse import unquote
    from core.data_models import Review
    raw_path = request.scope.get("raw_path", b"").decode("latin-1").lower()
    decoded_path = unquote(raw_path)
    if "%2f" in raw_path or "%5c" in raw_path or "/../" in decoded_path:
        raise HTTPException(status_code=400, detail="Invalid review filename")

    full_path = Path(_resolve_run_path(run_dir))
    safe_filename = Path(filename).name
    if safe_filename != filename:
        raise HTTPException(status_code=400, detail="Invalid review filename")

    stem = Path(filename).stem
    reviews_dir = (full_path / "outputs" / "reviews").resolve()
    pdf_path = (reviews_dir / f"{stem}.pdf").resolve()

    if not str(pdf_path).startswith(str(reviews_dir)):
        raise HTTPException(status_code=403, detail="Access denied")

    # If PDF exists on disk, return it directly
    if pdf_path.is_file():
        return FileResponse(
            str(pdf_path),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{pdf_path.name}"'}
        )

    # Otherwise, generate it from progress.json review data
    progress_file = full_path / "progress.json"
    review = None
    if progress_file.is_file():
        try:
            with open(progress_file, "r", encoding="utf-8") as f:
                pdata = json.load(f)
            papers_dict = pdata.get("papers", {})
            for pinfo in papers_dict.values():
                r_dict = pinfo.get("review")
                if r_dict:
                    p_stem = Path(r_dict.get("paper_filename", "")).stem
                    if p_stem in stem or stem in p_stem or r_dict.get("paper_id") in stem:
                        review = Review.model_validate(r_dict)
                        break
            if not review and len(papers_dict) == 1:
                pinfo = next(iter(papers_dict.values()))
                if pinfo.get("review"):
                    review = Review.model_validate(pinfo["review"])
        except Exception as e:
            print(f"[ExportPDF] Error reading review from progress.json: {e}")

    if not review:
        raise HTTPException(status_code=404, detail="Review data not found for PDF export")

    from utilities.output_generator import save_review_pdf
    save_review_pdf(review, output_path=str(pdf_path))

    return FileResponse(
        str(pdf_path),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{pdf_path.name}"'}
    )


# ---------------------------------------------------------------------------
# Judge endpoints
# ---------------------------------------------------------------------------

_active_judges: Dict[str, bool] = {}


def _run_judge_pipeline(run_dir: str, run_id: str):
    """Run compare + judge in a background thread."""
    from compare_reports import find_discrepancies
    from judge_conflicts import adjudicate_conflicts

    emitter = get_emitter()

    try:
        _active_judges[run_id] = True

        # Stage 1: Compare reports
        emitter.emit(StageStarted(stage_name="Judge-Compare", paper_filename=""))
        t0 = time.time()

        # find_discrepancies prints to stdout; we capture the key info ourselves
        reports_dir = os.path.join(run_dir, "outputs", "reports")
        csv_files = sorted(glob.glob(os.path.join(reports_dir, "report_consolidated_*.csv")))
        if len(csv_files) < 2:
            emitter.emit(Error(stage_name="Judge-Compare",
                               message="Need at least 2 consolidated reports to compare. Run reviews with different models first.",
                               recoverable=False))
            return

        find_discrepancies(run_dir)

        discrepancy_files = sorted(glob.glob(os.path.join(reports_dir, "HUMAN_REVIEW_discrepancies_*.csv")))
        if not discrepancy_files:
            emitter.emit(StageCompleted(stage_name="Judge-Compare", duration_s=time.time() - t0,
                                        result_summary="No conflicts found"))
            emitter.emit(RunCompleted(total_papers=0, total_cost=0.0, total_time_s=time.time() - t0,
                                      output_dir=os.path.join(run_dir, "outputs")))
            return

        import pandas as pd
        disc_df = pd.read_csv(discrepancy_files[-1])
        conflict_count = disc_df["paper_filename"].nunique() if "paper_filename" in disc_df.columns else 0
        emitter.emit(StageCompleted(stage_name="Judge-Compare", duration_s=time.time() - t0,
                                    result_summary=f"{conflict_count} conflicts found"))

        # Stage 2: Adjudicate
        emitter.emit(StageStarted(stage_name="Judge-Adjudicate", paper_filename=""))
        t1 = time.time()
        adjudicate_conflicts(run_dir)
        emitter.emit(StageCompleted(stage_name="Judge-Adjudicate", duration_s=time.time() - t1,
                                    result_summary="Done"))

        emitter.emit(RunCompleted(total_papers=conflict_count, total_cost=0.0,
                                  total_time_s=time.time() - t0,
                                  output_dir=os.path.join(run_dir, "outputs")))
    except Exception as e:
        import traceback
        emitter.emit(Error(stage_name="judge", message=str(e), recoverable=False))
        traceback.print_exc()
    finally:
        _active_judges.pop(run_id, None)


@app.post("/api/judge/{run_dir:path}")
async def start_judge(run_dir: str):
    """Start the compare + judge pipeline for a run directory."""
    full_path = _resolve_run_path(run_dir)

    reports_dir = os.path.join(full_path, "outputs", "reports")
    csv_files = glob.glob(os.path.join(reports_dir, "report_consolidated_*.csv"))
    if len(csv_files) < 2:
        raise HTTPException(
            status_code=400,
            detail="Need at least 2 consolidated reports. Run reviews with different models first.",
        )

    run_id = f"judge_{os.path.basename(run_dir)}_{int(time.time())}"

    emitter = get_emitter()
    sse = get_sse_backend()
    if not sse:
        sse = SSEBackend()
        emitter.add_backend(sse)

    thread = threading.Thread(target=_run_judge_pipeline, args=(full_path, run_id), daemon=True)
    thread.start()

    return {"run_id": run_id, "status": "started"}


@app.get("/api/judge/results/{run_dir:path}")
async def get_judge_results(run_dir: str):
    """Return the latest judge verdicts CSV as JSON."""
    import pandas as pd
    full_path = _resolve_run_path(run_dir)
    reports_dir = os.path.join(full_path, "outputs", "reports")
    if not os.path.isdir(reports_dir):
        return {"verdicts": []}
    verdict_files = sorted(glob.glob(os.path.join(reports_dir, "JUDGE_VERDICTS_report_*.csv")), reverse=True)
    if not verdict_files:
        return {"verdicts": []}
    try:
        df = pd.read_csv(verdict_files[0])
        return {"verdicts": df.fillna("").to_dict(orient="records")}
    except Exception:
        return {"verdicts": []}


@app.get("/api/judge/status/{run_id}")
async def judge_status(run_id: str):
    if run_id in _active_judges:
        return {"status": "running"}
    return {"status": "completed"}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Agentic Paper Review System - Web Dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8050, help="Port to bind (default: 8050)")
    args = parser.parse_args()

    import uvicorn
    browser_host = "localhost" if args.host in ("127.0.0.1", "0.0.0.0") else args.host
    print(f"Starting Agentic Paper Review System dashboard on http://{browser_host}:{args.port} (bound to {args.host}:{args.port})")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
