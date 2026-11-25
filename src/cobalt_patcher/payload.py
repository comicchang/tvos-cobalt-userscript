"""Userscript loader JS and CSP header prefix builders."""

from __future__ import annotations

import json
from typing import Iterable
from urllib.parse import urlparse

DEFAULT_LOCAL_USERSCRIPT_URL = "http://127.0.0.1:8787/api/userscripts.js"
DEFAULT_FALLBACK_USERSCRIPT_URL = (
    "https://cdn.jsdelivr.net/npm/@foxreis/tizentube/dist/userScript.js"
)


def extract_csp_token(url: str) -> str | None:
    """Return a CSP source token for a URL (scheme+host+port for http, host for https)."""
    if not url:
        return None
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        return None
    if parsed.scheme == "https" and parsed.port is None:
        return parsed.hostname
    return f"{parsed.scheme}://{parsed.netloc}"


def build_csp_prefix(
    base_origins: Iterable[str],
    primary_url: str,
    fallback_url: str | None = None,
    extra_origins: Iterable[str] = (),
) -> bytes:
    """Assemble a deduplicated space-terminated ASCII CSP source prefix."""
    tokens: list[str] = []

    def add(tok: str | None) -> None:
        if not tok:
            return
        cleaned = tok.strip()
        if cleaned and cleaned not in tokens:
            tokens.append(cleaned)

    for origin in base_origins:
        add(origin)
    add(extract_csp_token(primary_url))
    if fallback_url:
        add(extract_csp_token(fallback_url))
    for origin in extra_origins:
        add(origin)

    if not tokens:
        raise ValueError("CSP origin list cannot be empty")
    text = " ".join(tokens) + " "
    return text.encode("ascii")


def build_loader_js(
    primary_url: str = DEFAULT_LOCAL_USERSCRIPT_URL,
    fallback_url: str | None = DEFAULT_FALLBACK_USERSCRIPT_URL,
    patch_4k: bool = True,
) -> bytes:
    """Generate a compact ASCII JS bootstrapper injected into Cobalt's web context."""
    if not primary_url.strip():
        raise ValueError("primary_url must be non-empty")

    primary_lit = json.dumps(primary_url.strip())
    lines = [
        "(function(){",
        "if(document.__cobaltUsBoot)return;",
        "document.__cobaltUsBoot=true;",
        # Cobalt jumps directly from loading -> complete without firing DOMContentLoaded.
        "var dcl=false;",
        "document.addEventListener('DOMContentLoaded',function(){dcl=true;});",
        "window.addEventListener('load',function(){",
        "if(dcl)return;",
        "document.dispatchEvent(new Event('DOMContentLoaded'));",
        "window.dispatchEvent(new Event('DOMContentLoaded'));",
        "});",
        "function appendScript(url,onErr){",
        "var s=document.createElement('script');",
        "s.src=url+(url.indexOf('?')>=0?'&':'?')+'v='+Date.now();",
        "s.async=true;",
        "if(onErr)s.onerror=onErr;",
        "document.head.appendChild(s);",
        "}",
    ]
    if fallback_url and fallback_url.strip() and fallback_url.strip() != primary_url.strip():
        fallback_lit = json.dumps(fallback_url.strip())
        lines.append(
            f"appendScript({primary_lit},function(){{appendScript({fallback_lit});}});"
        )
    else:
        lines.append(f"appendScript({primary_lit});")

    if patch_4k:
        lines.extend(
            [
                "if(window.MediaSource&&window.MediaSource.isTypeSupported){",
                "var orig=window.MediaSource.isTypeSupported.bind(window.MediaSource);",
                "window.MediaSource.isTypeSupported=function(m){",
                "var parts=String(m).split(';').map(function(p){return p.trim();}).filter(function(p){",
                "return p&&!p.startsWith('width=')&&!p.startsWith('height=');",
                "});",
                "return orig(parts.join('; '));",
                "};",
                "}",
            ]
        )

    lines.append("})();")
    return "\n".join(lines).encode("ascii")
