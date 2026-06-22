"""Bot-probe target selection + execution.

Strategy (spec §5): probe every managed site each run, plus a rotating daily
sample of unmanaged sites so the whole fleet is covered ~fortnightly.
Probe-on-alert is wired in collect.py (alerting sites added to the target set).
"""
from __future__ import annotations
import os
import subprocess

PROBE_BOTS = [
    ("Browser",       "Mozilla/5.0 (Windows NT 10.0) Chrome/120.0",                                              200),
    ("GPTBot",        "Mozilla/5.0 (compatible; GPTBot/1.0; +https://openai.com/gptbot)",                         200),
    ("ClaudeBot",     "Mozilla/5.0 (compatible; ClaudeBot/1.0; +claudebot@anthropic.com)",                        200),
    ("PerplexityBot", "Mozilla/5.0 (compatible; PerplexityBot/1.0)",                                              200),
    ("Bytespider",    "Mozilla/5.0 (compatible; Bytespider; spider-feedback@bytedance.com)",                      403),
    ("Diffbot",       "Mozilla/5.0 (compatible; Diffbot/0.1)",                                                    403),
    ("Amazonbot",     "Mozilla/5.0 (compatible; Amazonbot/0.1; +https://developer.amazon.com/support/amazonbot)", 403),
]

SAMPLE_SIZE = 20


def select_probe_targets(roster: list[dict], managed_keys: set[str],
                         day_index: int, sample_size: int = SAMPLE_SIZE) -> list[str]:
    """Return the site keys to probe this run: all managed + a rotating unmanaged slice."""
    unmanaged = sorted(s["key"] for s in roster if s["key"] not in managed_keys)
    targets = set(managed_keys & {s["key"] for s in roster})
    if unmanaged and sample_size > 0:
        start = (day_index * sample_size) % len(unmanaged)
        window = (unmanaged + unmanaged)[start:start + sample_size]
        targets.update(window)
    return sorted(targets)


def curl_probe(url: str, ua: str, timeout: int = 12) -> int:
    """HTTP status code for a UA-spoofed HEAD request, or 0 on error.

    shell=True works around a curl error-43 quirk on Windows. UA/url are
    quote-escaped; the only callers pass hardcoded UAs + roster domains.
    """
    null = "NUL" if os.name == "nt" else "/dev/null"
    safe_ua = ua.replace('"', '\\"')
    safe_url = url.replace('"', '\\"')
    cmd = (f'curl -s -o {null} -w "%{{http_code}}" -I '
           f'-A "{safe_ua}" -L --max-time {timeout} "{safe_url}"')
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout + 5, shell=True)
        out = (r.stdout or "").strip()
        return int(out) if out.isdigit() else 0
    except Exception:
        return 0


def probe_site(apex: str) -> dict:
    """Run all PROBE_BOTS against https://www.{apex}/. Returns {ua: {http, expected, ok}}."""
    url = f"https://www.{apex}/"
    out = {}
    for label, ua, expected in PROBE_BOTS:
        code = curl_probe(url, ua)
        out[label] = {"http": code, "expected": expected, "ok": code == expected}
    return out
