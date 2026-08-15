"""
Shared utility helpers used across adapters.
"""

from __future__ import annotations


def split_message(text: str, max_length: int = 1950) -> list[str]:
    """
    Split a long message into chunks that respect max_length.
    Preserves markdown code blocks across splits!
    """
    if not text:
        return ["(empty response)"]

    if len(text) <= max_length:
        return [text]

    chunks = []
    current_chunk = ""
    in_code_block = False
    code_lang = ""

    for line in text.splitlines(keepends=True):
        if line.strip().startswith("```"):
            if not in_code_block:
                in_code_block = True
                code_lang = line.strip()[3:].strip()
            else:
                in_code_block = False
                code_lang = ""

        closing_buffer = 5 if in_code_block else 0
        if len(current_chunk) + len(line) + closing_buffer > max_length and current_chunk:
            if in_code_block:
                current_chunk += "```\n"
            chunks.append(current_chunk)
            
            if in_code_block:
                current_chunk = f"```{code_lang}\n{line}"
            else:
                current_chunk = line
        else:
            if len(line) > max_length:
                if current_chunk:
                    chunks.append(current_chunk)
                for i in range(0, len(line), max_length):
                    chunks.append(line[i : i + max_length])
                current_chunk = ""
            else:
                current_chunk += line

    if current_chunk:
        chunks.append(current_chunk)

    return chunks or [text[:max_length]]


def truncate(text: str, max_chars: int = 200, suffix: str = "...") -> str:
    """Truncate text to max_chars, adding suffix if truncated."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - len(suffix)] + suffix
