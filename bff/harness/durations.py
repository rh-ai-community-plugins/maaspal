import re

_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h|d)?\s*$")
_UNIT_S = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration_s(value: object) -> float:
    """Seconds from a number or a Kuadrant-style window string ("30s", "1m",
    "24h") — the format MaaSSubscription tokenRateLimits[].window uses. A bare
    number is already seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    m = _DURATION_RE.match(str(value))
    if not m:
        raise ValueError(f"Cannot parse duration: {value!r} (expected e.g. 30, '30s', '1m', '24h')")
    return float(m.group(1)) * _UNIT_S[m.group(2) or "s"]
