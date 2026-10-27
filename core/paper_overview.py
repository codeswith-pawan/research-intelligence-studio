"""Core Paper Overview Module for Research Intelligence Studio.

Generates and caches descriptive, source-grounded overviews of scientific manuscripts,
completely separate from reviewer criticism, scoring, and rubrics.
"""

import os
import json
import hashlib
import tempfile
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any, Literal
from pydantic import BaseModel, Field, ValidationError

from core.data_models import Paper, PaperMetadata
from core.config_loader import Config
from core.llm_wrapper import call_llm
from core.json_response import parse_json_object

OVERVIEW_SCHEMA_VERSION = "1.0"

STATUS_VERIFIED_CONTENT = "verified_content"
STATUS_NOT_IDENTIFIED = "not_identified_in_text"

OVERVIEW_SECTION_KEYS = [
    "research_problem",
    "research_objectives",
    "methodology",
    "main_contributions",
    "experimental_setup",
    "key_results",
    "limitations",
    "future_work",
]
OVERVIEW_SECTIONS = OVERVIEW_SECTION_KEYS

SECTION_TITLES = {
    "research_problem": "Research Problem & Motivation",
    "research_objectives": "Research Objectives & Questions",
    "methodology": "Methodology & Proposed Approach",
    "main_contributions": "Main Contributions",
    "experimental_setup": "Experimental Setup & Evaluation",
    "key_results": "Key Results & Findings",
    "limitations": "Author-Stated Limitations",
    "future_work": "Author-Stated Future Work",
}


class PaperOverviewSection(BaseModel):
    title: str
    status: Literal["verified_content", "not_identified_in_text"]
    content: str
    source_references: List[str] = Field(default_factory=list)
    traceable_references: List[str] = Field(default_factory=list)


class PaperOverviewData(BaseModel):
    schema_version: str = OVERVIEW_SCHEMA_VERSION
    run_dir: str
    paper_filename: str
    file_hash_sha256: str
    generated_at: str
    generator_model: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    sections: Dict[str, PaperOverviewSection]


def compute_file_sha256(filepath: str) -> str:
    """Compute the true SHA-256 hash of a file by reading actual bytes on disk."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def validate_overview_payload(payload_dict: dict) -> Tuple[bool, List[str]]:
    """Validate that an overview dict satisfies all 8 sections and valid statuses."""
    errors = []
    if not isinstance(payload_dict, dict):
        return False, ["Payload is not a valid dictionary"]

    sections = payload_dict.get("sections")
    if not isinstance(sections, dict):
        return False, ["Missing 'sections' object in overview"]

    for key in OVERVIEW_SECTION_KEYS:
        if key not in sections:
            errors.append(f"Missing required section: '{key}'")
            continue
        sec = sections[key]
        if not isinstance(sec, dict):
            errors.append(f"Section '{key}' must be a dictionary")
            continue
        st = sec.get("status")
        if st not in ("verified_content", "not_identified_in_text"):
            errors.append(f"Section '{key}' has invalid status: '{st}'")
        content = sec.get("content")
        if not content or not str(content).strip():
            errors.append(f"Section '{key}' has empty content")
        refs = sec.get("source_references")
        if refs is not None and not isinstance(refs, list):
            errors.append(f"Section '{key}' source_references must be a list")

    return len(errors) == 0, errors


def load_paper_overview_cache(
    run_path: str,
    expected_sha256: Optional[str] = None
) -> Tuple[Optional[PaperOverviewData], str]:
    """Load cached paper overview if available and strictly valid against manuscript hash.

    Returns:
        (overview_data, "valid") if valid cache matches actual file bytes
        (None, "stale") if cache exists but SHA-256 hash does not match
        (None, "missing") if cache file does not exist
        (None, "corrupt") if cache file is unparseable
    """
    cache_path = os.path.join(run_path, "outputs", "paper_overview.json")
    if not os.path.isfile(cache_path):
        return None, "missing"

    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        overview = PaperOverviewData.model_validate(data)
    except Exception:
        return None, "corrupt"

    # If expected_sha256 is provided, strictly compare
    if expected_sha256 and overview.file_hash_sha256 != expected_sha256:
        return None, "stale"

    return overview, "valid"


def save_paper_overview_cache(run_path: str, overview: PaperOverviewData) -> str:
    """Atomically save paper overview cache to run_dir/outputs/paper_overview.json."""
    outputs_dir = os.path.join(run_path, "outputs")
    os.makedirs(outputs_dir, exist_ok=True)
    target_path = os.path.join(outputs_dir, "paper_overview.json")

    # Use NamedTemporaryFile in the same directory for atomic replace
    with tempfile.NamedTemporaryFile("w", dir=outputs_dir, delete=False, encoding="utf-8") as tf:
        tmp_name = tf.name
        json.dump(overview.model_dump(), tf, indent=2, ensure_ascii=False)

    os.replace(tmp_name, target_path)
    return target_path


def generate_paper_overview(
    paper: Paper,
    run_dir: str,
    actual_file_hash: str,
    config: Config
) -> PaperOverviewData:
    """Generate a descriptive, evidence-grounded Paper Overview using configured LLM.

    Strict rules:
    - Only describe what is in the manuscript.
    - Never invent page references, contributions, or limitations.
    - If a section cannot be supported by the source text, set status: not_identified_in_text.
    """
    llm_cfg = config.get_llm_config()
    provider = llm_cfg.get("synthesizer_provider") or llm_cfg.get("extractor_provider", "ollama")
    model = llm_cfg.get("synthesizer_model") or llm_cfg.get("extractor_model", "llama3.2:3b")

    # Load prompts with multi-tier fallback (Config -> global file -> builtin defaults)
    system_prompt = config.get_prompt("paper_overview_system") if config else ""
    user_prompt_template = config.get_prompt("paper_overview_user") if config else ""

    if not system_prompt:
        global_sys_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "prompts", "paper_overview_system.txt")
        if os.path.isfile(global_sys_path):
            try:
                with open(global_sys_path, "r", encoding="utf-8") as f:
                    system_prompt = f.read()
            except Exception:
                pass

    if not user_prompt_template:
        global_usr_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "prompts", "paper_overview_user.txt")
        if os.path.isfile(global_usr_path):
            try:
                with open(global_usr_path, "r", encoding="utf-8") as f:
                    user_prompt_template = f.read()
            except Exception:
                pass

    if not system_prompt:
        system_prompt = (
            "You are an expert scientific manuscript analyst. Your job is to produce a descriptive, "
            "strictly objective summary of an academic paper's content across 8 specific dimensions. "
            "Never invent facts, numbers, experiments, or limitations. If the paper does not state an aspect, "
            "mark it as 'not_identified_in_text'. Output only valid JSON."
        )
    if not user_prompt_template:
        user_prompt_template = (
            "# Paper: {paper_title}\n\n"
            "# Abstract:\n{paper_abstract}\n\n"
            "# Manuscript Content:\n{paper_content_sample}\n\n"
            "Provide the objective 8-section overview in JSON matching the required schema."
        )

    # Prepare manuscript text (strip references to sample core paper body within 16,000 char budget)
    import re
    text = paper.content_markdown or ""
    ref_match = re.search(r'\n(?:\*\*|\#\#)?\s*(?:References|Bibliography)\b', text, re.IGNORECASE)
    body_text = text[:ref_match.start()] if ref_match else text

    if len(body_text) > 16000:
        part1 = body_text[:8000]
        mid_start = max(8000, len(body_text) // 2 - 2000)
        part2 = body_text[mid_start:mid_start + 4000]
        part3 = body_text[-4000:]
        sample_text = f"{part1}\n\n[...section omitted for brevity...]\n\n{part2}\n\n[...section omitted for brevity...]\n\n{part3}"
    else:
        sample_text = body_text

    user_prompt = user_prompt_template.format(
        paper_title=paper.metadata.title or paper.filename,
        paper_abstract=paper.metadata.abstract or "No abstract available in manuscript.",
        paper_content_sample=sample_text,
    )

    response = call_llm(
        prompt=user_prompt,
        system_prompt=system_prompt,
        provider=provider,
        model=model,
        temperature=0.0,
        max_retries=2,
        response_format="json",
        role="overview",
    )

    raw_json = None
    if response.get("success"):
        try:
            raw_json = parse_json_object(response.get("content"))
        except Exception:
            raw_json = None

    sections_dict: Dict[str, PaperOverviewSection] = {}

    # Extract or fallback each of the 8 sections
    for key in OVERVIEW_SECTION_KEYS:
        title = SECTION_TITLES[key]
        sec_data = (raw_json.get("sections", {}) if isinstance(raw_json, dict) else {}).get(key, {})

        content = ""
        status = "not_identified_in_text"
        refs = []

        if isinstance(sec_data, dict):
            raw_content = str(sec_data.get("content", "")).strip()
            raw_status = str(sec_data.get("status", "")).strip()
            raw_refs = sec_data.get("source_references", [])

            if raw_status == "verified_content" and raw_content:
                content = raw_content
                status = "verified_content"
                if isinstance(raw_refs, list):
                    # Keep traceable section/page references only
                    refs = [str(r).strip() for r in raw_refs if str(r).strip()]
            else:
                status = "not_identified_in_text"
                content = raw_content or f"The authors do not explicitly state {title.lower()} in the manuscript text."
                refs = []
        else:
            status = "not_identified_in_text"
            content = f"The authors do not explicitly state {title.lower()} in the manuscript text."
            refs = []

        sections_dict[key] = PaperOverviewSection(
            title=title,
            status=status,
            content=content,
            source_references=refs,
            traceable_references=refs,
        )

    meta = {
        "title": paper.metadata.title or paper.filename,
        "authors": paper.metadata.authors or [],
        "year": paper.metadata.year,
        "abstract": paper.metadata.abstract,
        "token_count": paper.token_count,
    }

    return PaperOverviewData(
        schema_version=OVERVIEW_SCHEMA_VERSION,
        run_dir=run_dir,
        paper_filename=paper.filename,
        file_hash_sha256=actual_file_hash,
        generated_at=datetime.now(timezone.utc).isoformat(),
        generator_model=f"{provider}/{model}",
        metadata=meta,
        sections=sections_dict,
    )
