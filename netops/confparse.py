"""Compare router configs without being fooled by ordering or comments."""
from __future__ import annotations

_SKIP_PREFIXES = ("!", "building configuration", "current configuration", "frr version", "frr defaults",
                  "service integrated-vtysh-config", "line vty", "end")
_SKIP_EXACT = {"exit", "exit-address-family", "exit-vrf"}


def normalise(text: str) -> str:
    """Strip the volatile/noisy lines so two dumps of the same config are identical."""
    keep = []
    for raw in text.replace("\r", "").split("\n"):
        line = raw.rstrip()
        s = line.strip()
        if not s or s.lower().startswith(_SKIP_PREFIXES):
            continue
        keep.append(line)
    return "\n".join(keep) + "\n"


def parse(text: str) -> set[str]:
    """Flatten a config into 'parent > child | line' strings (a set, so order does not matter)."""
    out: set[str] = set()
    stack: list[tuple[int, str]] = []
    for raw in text.replace("\r", "").split("\n"):
        s = raw.strip()
        if not s or s.lower().startswith(_SKIP_PREFIXES) or s in _SKIP_EXACT:
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        ctx = " > ".join(t for _, t in stack)
        out.add(f"{ctx} | {s}" if ctx else s)
        stack.append((indent, s))
    return out


def missing(intended: str, running: str) -> list[str]:
    """Lines the router SHOULD have but does not."""
    return sorted(parse(intended) - parse(running))


def changed(before: str, after: str) -> tuple[list[str], list[str]]:
    """(added, removed) between two dumps of the same router."""
    a, b = parse(before), parse(after)
    return sorted(b - a), sorted(a - b)
