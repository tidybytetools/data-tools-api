"""Turn nested JSON into a flat dict with dotted keys."""

from __future__ import annotations

from typing import Any


def flatten(obj: Any, prefix: str = "", sep: str = ".") -> dict[str, Any]:
    """Flatten dicts and lists. `a.b.0` means item 0 of list `b` inside `a`."""
    if sep == "":
        raise ValueError("separator cannot be empty")
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        if not obj:
            if prefix:
                out[prefix] = {}
            return out
        for key, value in obj.items():
            child = f"{prefix}{sep}{key}" if prefix else str(key)
            out.update(flatten(value, child, sep))
        return out
    if isinstance(obj, list):
        if not obj:
            if prefix:
                out[prefix] = []
            return out
        for i, value in enumerate(obj):
            child = f"{prefix}{sep}{i}" if prefix else str(i)
            out.update(flatten(value, child, sep))
        return out
    if prefix:
        out[prefix] = obj
    else:
        out[""] = obj
    return out
