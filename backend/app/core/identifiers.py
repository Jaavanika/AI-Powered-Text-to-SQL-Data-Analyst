import re
import unicodedata

MAX_IDENTIFIER_LENGTH = 63


def sanitize_identifier(raw: object, fallback: str = "col") -> str:
    """Turn arbitrary text into a safe lowercase snake_case SQL identifier."""
    text = unicodedata.normalize("NFKD", str(raw)).encode("ascii", "ignore").decode()
    text = re.sub(r"[^0-9a-zA-Z]+", "_", text).strip("_").lower()
    if not text:
        text = fallback
    if text[0].isdigit():
        text = f"{fallback}_{text}"
    if text.startswith("sqlite_"):
        text = f"t_{text}"
    return text[:MAX_IDENTIFIER_LENGTH]


def unique_identifiers(raw_names: list[object], fallback: str = "col") -> list[str]:
    """Sanitize each name and make the results unique, keeping the original order."""
    seen: set[str] = set()
    result: list[str] = []
    for raw in raw_names:
        base = sanitize_identifier(raw, fallback)
        name, n = base, 2
        while name in seen:
            suffix = f"_{n}"
            name = base[: MAX_IDENTIFIER_LENGTH - len(suffix)] + suffix
            n += 1
        seen.add(name)
        result.append(name)
    return result
