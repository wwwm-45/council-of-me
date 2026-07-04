from __future__ import annotations


def validate_evidence_quotes(output_text: str, quotes: list[str]) -> tuple[bool, list[str]]:
    missing = [quote for quote in quotes if quote and quote not in output_text]
    return (not missing, missing)
