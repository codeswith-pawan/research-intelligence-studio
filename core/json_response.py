"""Conservative parsing helpers for JSON-only model responses."""
import json
from typing import Any

from json_repair import repair_json


def parse_json_object(raw_content: Any) -> dict:
    """Parse a JSON object, allowing surrounding prose and repairing only as fallback."""
    if not isinstance(raw_content, str):
        raise ValueError("Model response content must be text.")

    decoder = json.JSONDecoder()
    parse_error = None
    for start, char in enumerate(raw_content):
        if char != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(raw_content[start:])
        except json.JSONDecodeError as exc:
            parse_error = exc
            continue
        if isinstance(candidate, dict):
            return candidate

    try:
        repaired = repair_json(raw_content, return_objects=True)
    except Exception as exc:
        raise ValueError(f"JSON repair failed: {type(exc).__name__}.") from exc

    if isinstance(repaired, dict):
        return repaired
    if parse_error is not None:
        raise ValueError("No JSON object could be parsed from the response.") from parse_error
    raise ValueError("Model response did not contain a JSON object.")
