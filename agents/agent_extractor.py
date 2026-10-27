import json
import math
import os
import re
import threading
import unicodedata
from collections import Counter
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict, Any, Optional

import pymupdf
from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr, ValidationError, field_validator

from core.data_models import Paper, Extraction, EvidenceItem
from core.config_loader import Config
from core.llm_wrapper import call_llm, build_cached_messages
from core.json_response import parse_json_object
from utilities.helpers import get_scale_definition


class _EvidencePayload(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    quote: StrictStr
    page_reference: StrictStr
    relevance: StrictStr


class _ExtractionPayload(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    score: StrictInt
    score_justification: StrictStr
    evidence: List[_EvidencePayload]
    strengths: List[StrictStr]
    weaknesses: List[StrictStr]
    confidence: float

    @field_validator("confidence", mode="before")
    @classmethod
    def validate_confidence(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("confidence must be numeric")
        if not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        return float(value)


_QUERY_STOP_WORDS = {
    "about", "after", "against", "also", "among", "and", "any", "are", "assess",
    "based", "being", "between", "could", "each", "evidence", "from", "have", "into",
    "only", "paper", "specific", "such", "that", "the", "their", "them", "these", "this",
    "those", "through", "whether", "which", "with", "work", "would",
}
_SOURCE_WORD_PATTERN = re.compile(r"[a-z0-9]+")


def _query_word(term: str) -> str:
    if len(term) > 4 and term.endswith("ies"):
        return term[:-3] + "y"
    if len(term) > 4 and term.endswith("s") and not term.endswith("ss"):
        return term[:-1]
    return term


def _criterion_query_weights(criterion: Dict[str, Any]) -> Dict[str, float]:
    fields = [
        (criterion.get("id", ""), 3.0),
        (criterion.get("name", ""), 3.0),
        (criterion.get("description", ""), 1.0),
        (" ".join(str(item) for item in criterion.get("sub_questions", [])), 2.0),
    ]
    weights = Counter()
    for value, weight in fields:
        for raw_term in _SOURCE_WORD_PATTERN.findall(str(value).lower()):
            term = _query_word(raw_term)
            if len(term) >= 3 and term not in _QUERY_STOP_WORDS:
                weights[term] += weight
    return dict(weights)


def _split_source_windows(text: str, page_number: Optional[int], window_chars: int = 1100):
    """Split source text into overlapping windows that remain exact source substrings."""
    if not text or not text.strip():
        return []
    if len(text) <= window_chars:
        return [{"page": page_number, "start": 0, "text": text.strip()}]

    windows = []
    start = 0
    overlap = 120
    while start < len(text):
        end = min(start + window_chars, len(text))
        if end < len(text):
            boundary = max(
                text.rfind("\n", start + window_chars * 2 // 3, end),
                text.rfind(" ", start + window_chars * 2 // 3, end),
            )
            if boundary > start:
                end = boundary
        segment = text[start:end].strip()
        if segment:
            windows.append({"page": page_number, "start": start, "text": segment})
        if end >= len(text):
            break
        next_start = max(start + 1, end - overlap)
        next_word_boundary = text.find(" ", next_start, min(next_start + 80, len(text)))
        if next_word_boundary >= 0:
            next_start = next_word_boundary + 1
        start = next_start
    return windows


def _select_relevant_source_windows(
    pages: List[str],
    criterion: Dict[str, Any],
    max_source_chars: int,
    max_windows: int = 4,
):
    """Select a small, page-aware set of verbatim excerpts for short-context models."""
    candidates = []
    for page_number, page_text in enumerate(pages, start=1):
        candidates.extend(_split_source_windows(page_text, page_number))
    if not candidates:
        return []

    query_weights = _criterion_query_weights(criterion)
    if not query_weights:
        return candidates[:max_windows]

    token_counts = [
        Counter(_query_word(term) for term in _SOURCE_WORD_PATTERN.findall(item["text"].lower()))
        for item in candidates
    ]
    document_frequency = Counter(
        term
        for counts in token_counts
        for term in set(counts)
    )
    candidate_count = len(candidates)
    for item, counts in zip(candidates, token_counts):
        score = 0.0
        for term, query_weight in query_weights.items():
            frequency = counts.get(term, 0)
            if frequency:
                inverse_frequency = math.log(
                    1.0 + (candidate_count - document_frequency[term] + 0.5)
                    / (document_frequency[term] + 0.5)
                )
                score += query_weight * math.log1p(frequency) * inverse_frequency
        item["score"] = score

    ranked = sorted(candidates, key=lambda item: (item["score"], len(item["text"])), reverse=True)
    selected = []
    used_pages = set()
    used_chars = 0
    for item in ranked:
        page_number = item["page"]
        marker_cost = len(f"<PDF_PAGE_{page_number}_START><PDF_PAGE_{page_number}_END>") + 2
        item_cost = len(item["text"]) + marker_cost
        if item["score"] <= 0 or page_number in used_pages:
            continue
        if used_chars + item_cost > max_source_chars:
            continue
        selected.append(item)
        used_pages.add(page_number)
        used_chars += item_cost
        if len(selected) >= max_windows:
            break

    if not selected:
        selected = candidates[:max_windows]
    return sorted(selected, key=lambda item: (item["page"] or 0, item["start"]))


def _canonical_text_and_offsets(text: str):
    """Canonicalize only Unicode compatibility, whitespace, and PDF line hyphenation."""
    chars = []
    starts = []
    ends = []
    index = 0

    while index < len(text):
        char = text[index]

        # PDF extraction can split a word at a line ending with a hyphen (including Unicode soft hyphen \u00ad).
        if char in {"-", "\u2010", "\u00ad"} and index > 0 and text[index - 1].isalnum():
            next_index = index + 1
            saw_line_break = False
            while next_index < len(text) and text[next_index].isspace():
                saw_line_break = saw_line_break or text[next_index] in "\r\n"
                next_index += 1
            if (
                saw_line_break
                and next_index < len(text)
                and text[next_index].isalnum()
            ):
                index = next_index
                continue

        # Soft hyphen without a line break is a discretionary hyphen inside a word; skip it.
        if char == "\u00ad":
            index += 1
            continue

        # Normalize typographical smart quotes and dashes to standard ASCII equivalents
        if char in {"\u201c", "\u201d"}:
            char = '"'
        elif char in {"\u2018", "\u2019"}:
            char = "'"
        elif char in {"\u2013", "\u2014"}:
            char = "-"

        if char.isspace():
            if chars and chars[-1] == " ":
                ends[-1] = index + 1
            else:
                chars.append(" ")
                starts.append(index)
                ends.append(index + 1)
            index += 1
            continue

        expanded = unicodedata.normalize("NFKC", char)
        for normalized_char in expanded:
            chars.append(normalized_char)
            starts.append(index)
            ends.append(index + 1)
        index += 1

    while chars and chars[0] == " ":
        chars.pop(0)
        starts.pop(0)
        ends.pop(0)
    while chars and chars[-1] == " ":
        chars.pop()
        starts.pop()
        ends.pop()
    return "".join(chars), starts, ends


def _normalize_quote(text: str) -> str:
    """Normalize conservative source-text differences without dropping punctuation or case."""
    canonical, _, _ = _canonical_text_and_offsets(text)
    return canonical


def _find_source_substring(source_text: str, quote: str) -> Optional[str]:
    """Return the exact source-text span matching quote under conservative normalization."""
    source_canonical, source_starts, source_ends = _canonical_text_and_offsets(source_text)
    quote_canonical, _, _ = _canonical_text_and_offsets(quote)
    if not quote_canonical:
        return None

    index = source_canonical.find(quote_canonical)
    if index < 0:
        return None
    last = index + len(quote_canonical) - 1
    if last >= len(source_starts):
        return None
    return source_text[source_starts[index]:source_ends[last]]


@lru_cache(maxsize=8)
def _cached_pdf_page_texts(pdf_path: str, modified_ns: int, file_size: int):
    """Read page text in visual reading order, retaining the PDF's 1-based page map."""
    with pymupdf.open(pdf_path) as document:
        return tuple(page.get_text("text", sort=True) for page in document)


def _page_labeled_pdf_text(
    paper: Paper,
    criterion: Optional[Dict[str, Any]] = None,
    max_source_chars: Optional[int] = None,
) -> Optional[str]:
    """Build a page-labeled source, optionally selecting exact criterion-relevant excerpts."""
    path = Path(paper.original_path)
    if path.suffix.lower() != ".pdf":
        return None
    try:
        stat = path.stat()
        pages = _cached_pdf_page_texts(str(path), stat.st_mtime_ns, stat.st_size)
    except Exception:
        return None
    if not pages or sum(len(page.strip()) for page in pages) < 100:
        return None

    parts = []
    if criterion is not None and max_source_chars is not None:
        source_windows = _select_relevant_source_windows(
            list(pages), criterion, max_source_chars=max_source_chars
        )
        page_texts = [(item["page"], item["text"]) for item in source_windows]
    else:
        page_texts = list(enumerate(pages, start=1))
    for page_number, page_text in page_texts:
        parts.append(
            f"<PDF_PAGE_{page_number}_START>\n"
            f"{page_text.strip()}\n"
            f"<PDF_PAGE_{page_number}_END>"
        )
    return "\n\n".join(parts)


def _verify_pdf_quote(pdf_path: str, quote: str):
    """Return the exact extracted PDF substring and all matching 1-based page numbers."""
    if not quote or not quote.strip():
        return None, []

    try:
        with pymupdf.open(pdf_path) as document:
            matched_text = None
            matched_pages = []
            for page_number, page in enumerate(document, start=1):
                source_texts = [page.get_text(), page.get_text("text", sort=True)]
                exact_substring = None
                for source_text in source_texts:
                    exact_substring = _find_source_substring(source_text, quote)
                    if exact_substring is not None:
                        break
                if exact_substring is not None:
                    if matched_text is None:
                        matched_text = exact_substring
                    matched_pages.append(page_number)
            return matched_text, matched_pages
    except Exception:
        return None, []


def _find_quote_pdf_pages(pdf_path: str, quote: str) -> List[int]:
    """Return 1-based PDF page numbers for a conservative exact-text match."""
    _, pages = _verify_pdf_quote(pdf_path, quote)
    return pages


def _verify_evidence_quote(paper: Paper, quote: str):
    """Return source-exact displayed text, page numbers, and verification note."""
    if Path(paper.original_path).suffix.lower() == ".pdf":
        exact_quote, pages = _verify_pdf_quote(paper.original_path, quote)
        if exact_quote is not None:
            return exact_quote, pages, None
        return quote, [], "No conservative exact-text match was found in the source PDF."

    exact_quote = _find_source_substring(paper.content_markdown or "", quote)
    if exact_quote is not None:
        return exact_quote, [], None
    return quote, [], "No conservative exact-text match was found in extracted document text."


def _record_criterion_error(config, criterion_id: str, message: str):
    lock = getattr(config, "_extraction_failure_lock", None)
    if lock is None:
        lock = threading.Lock()
        setattr(config, "_extraction_failure_lock", lock)
    with lock:
        failures = getattr(config, "extraction_failures", None)
        if failures is None:
            failures = {}
            setattr(config, "extraction_failures", failures)
        failures[criterion_id] = message


def _validation_summary(error: ValidationError) -> str:
    parts = []
    for item in error.errors(include_input=False)[:8]:
        location = ".".join(str(piece) for piece in item.get("loc", ())) or "response"
        err_type = item.get("type", "")
        msg = item.get("msg", "invalid value")
        if "missing" in err_type:
            parts.append(f"Missing field '{location}'")
        elif "type" in err_type or "int" in err_type or "string" in err_type:
            parts.append(f"Wrong type for '{location}': {msg}")
        else:
            parts.append(f"{location}: {msg}")
    return "; ".join(parts) or "Response did not match the required extraction schema."


def build_extraction_prompt(
    paper_content: str,
    criterion: Dict[str, Any],
    domain: str,
    prompt_template: str,
    max_content_tokens: int = 100000
) -> str:
    """Build the user prompt for Agent 1."""
    if len(paper_content) > max_content_tokens * 4:
        print(f"[Warning] Truncating paper content for {criterion['id']}")
        paper_content = paper_content[:max_content_tokens * 4]

    return prompt_template.format(
        paper_markdown=paper_content,
        criterion_name=criterion['name'],
        criterion_description=criterion['description'],
        sub_questions="\n".join(f"- {q}" for q in criterion.get('sub_questions', [])),
        scale_definition=get_scale_definition(criterion.get('scale', {})),
        domain=domain
    )


def _build_criterion_prompt(criterion: Dict[str, Any], config: Config) -> Optional[str]:
    """Build the criterion-only user prompt from the dedicated template."""
    template = config.get_prompt("extractor_criterion")
    if not template:
        return None
    return template.format(
        criterion_name=criterion['name'],
        criterion_description=criterion['description'],
        sub_questions="\n".join(f"- {q}" for q in criterion.get('sub_questions', [])),
        scale_definition=get_scale_definition(criterion.get('scale', {})),
        domain=config.domain,
    )


def extract_criterion_evidence(
    paper: Paper,
    criterion: Dict[str, Any],
    config: Config
) -> Optional[Extraction]:
    """Extract one criterion, rejecting incomplete schemas and unverified scores safely."""
    criterion_id = criterion.get("id", "unknown_criterion")
    try:
        llm_config = config.get_llm_config()
        system_prompt = config.get_prompt("extractor_system").format(domain=config.domain)
        max_content_tokens = config.get_system_config()['max_content_tokens']
        source_char_budget = (
            4800 if llm_config["extractor_provider"].strip().lower() == "ollama" else None
        )
        paper_content = (
            _page_labeled_pdf_text(
                paper,
                criterion=criterion,
                max_source_chars=source_char_budget,
            )
            or paper.content_markdown
        )
        if len(paper_content) > max_content_tokens * 4:
            print(f"[Warning] Truncating paper content for {criterion_id}")
            paper_content = paper_content[:max_content_tokens * 4]

        criterion_prompt = _build_criterion_prompt(criterion, config)
        if criterion_prompt is not None:
            messages = build_cached_messages(system_prompt, paper_content, criterion_prompt)
            response = call_llm(
                prompt="",
                system_prompt="",
                provider=llm_config['extractor_provider'],
                model=llm_config['extractor_model'],
                temperature=llm_config['temperature'],
                max_retries=llm_config['max_retries'],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "extraction_payload",
                        "schema": _ExtractionPayload.model_json_schema(),
                    },
                },
                role="extraction",
                messages=messages,
            )
        else:
            prompt = build_extraction_prompt(
                paper_content=paper_content,
                criterion=criterion,
                domain=config.domain,
                prompt_template=config.get_prompt("extractor_user"),
                max_content_tokens=max_content_tokens,
            )
            response = call_llm(
                prompt=prompt,
                system_prompt=system_prompt,
                provider=llm_config['extractor_provider'],
                model=llm_config['extractor_model'],
                temperature=llm_config['temperature'],
                max_retries=llm_config['max_retries'],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "extraction_payload",
                        "schema": _ExtractionPayload.model_json_schema(),
                    },
                },
                role="extraction",
            )

        if not response.get("success"):
            err_msg = response.get("error") or "provider request failed"
            err_lower = str(err_msg).lower()
            if any(k in err_lower for k in ["connection", "connect", "timeout", "timed out", "time out", "refused", "unreachable"]):
                raise RuntimeError(f"Model unreachable: {err_msg}")
            elif any(k in err_lower for k in ["credit", "quota", "ratelimit", "rate limit", "429"]):
                raise RuntimeError(f"Provider quota or rate limit error: {err_msg}")
            else:
                raise RuntimeError(f"Provider request failed: {err_msg}")

        raw_content = response.get("content")
        if not raw_content or not str(raw_content).strip():
            raise ValueError("Model returned empty response.")

        try:
            parsed_json = parse_json_object(raw_content)
        except Exception as exc:
            raise ValueError(f"JSON parse error: {exc}") from exc

        payload = _ExtractionPayload.model_validate(parsed_json)

        scale = criterion.get("scale", {})
        score_range = scale.get("range") if isinstance(scale, dict) else None
        if isinstance(score_range, (list, tuple)) and len(score_range) == 2:
            low, high = int(score_range[0]), int(score_range[1])
        else:
            labels = scale.get("labels", {}) if isinstance(scale, dict) else {}
            try:
                label_scores = [int(value) for value in labels]
            except (TypeError, ValueError):
                label_scores = []
            low, high = (min(label_scores), max(label_scores)) if label_scores else (None, None)
        if low is not None and not low <= payload.score <= high:
            raise ValueError(f"score must be within the configured range {low}..{high}")
        if not payload.score_justification.strip():
            raise ValueError("score_justification must not be empty")
        if any(not item.strip() for item in payload.strengths + payload.weaknesses):
            raise ValueError("strengths and weaknesses must not contain empty items")

        evidence_items = []
        for item in payload.evidence:
            if not item.quote.strip():
                raise ValueError("evidence.quote must not be empty")
            if not item.relevance.strip():
                raise ValueError("evidence.relevance must not be empty")

            exact_quote, page_numbers, verification_note = _verify_evidence_quote(
                paper, item.quote
            )
            verified = verification_note is None
            if page_numbers:
                page_reference = "PDF Page " + ", ".join(str(page) for page in page_numbers)
            elif verified:
                page_reference = "N/A (DOCX page numbering unavailable)"
            else:
                page_reference = "N/A"

            evidence_items.append(EvidenceItem(
                quote=exact_quote,
                page_reference=page_reference,
                relevance=item.relevance,
                verification_status="Verified" if verified else "Unverified",
                pdf_page_numbers=page_numbers,
                verification_note=verification_note,
            ))

        extraction = Extraction(
            paper_id=paper.id,
            criterion_id=criterion_id,
            score=payload.score,
            score_justification=payload.score_justification,
            evidence=evidence_items,
            strengths=payload.strengths,
            weaknesses=payload.weaknesses,
            confidence=payload.confidence,
            model_used=f"{llm_config['extractor_provider']}/{llm_config['extractor_model']}",
            cost=float(response.get("cost", 0.0) or 0.0),
        )
        return extraction

    except ValidationError as exc:
        message = f"Schema validation error - {_validation_summary(exc)}"
    except ValueError as exc:
        message = f"Invalid response format: {exc}"
    except RuntimeError as exc:
        message = str(exc)
    except Exception as exc:
        message = f"{type(exc).__name__}: {str(exc)[:180]}"
    criterion_error_message = f"Criterion '{criterion_id}': {message}"
    _record_criterion_error(config, criterion_id, criterion_error_message)
    print(f"[Agent 1 Error] {criterion_id}: {message}")
    return None


def process_paper_extractions(
    paper: Paper,
    config: Config
) -> List[Extraction]:
    """Run Agent 1 on all configured criteria, retaining criterion failure reasons."""
    setattr(config, "extraction_failures", {})
    setattr(config, "_extraction_failure_lock", threading.Lock())
    criteria = config.get_criteria()
    llm_config = config.get_llm_config()
    extractions = []

    print(f"[Agent 1] Starting parallel extraction for: {paper.filename}")
    with ThreadPoolExecutor(max_workers=llm_config['max_parallel']) as executor:
        futures = {
            executor.submit(extract_criterion_evidence, paper, criterion, config): criterion
            for criterion in criteria
        }
        for future in as_completed(futures):
            criterion = futures[future]
            try:
                result = future.result()
                if result:
                    extractions.append(result)
                    print(f"  > Completed criterion: {criterion['id']}")
                else:
                    print(f"  > FAILED criterion: {criterion['id']}")
            except Exception as exc:
                message = f"{type(exc).__name__}: {str(exc)[:180]}"
                _record_criterion_error(config, criterion['id'], message)
                print(f"[Agent 1 Error] {criterion['id']}: {message}")

    return sorted(extractions, key=lambda extraction: extraction.criterion_id)
