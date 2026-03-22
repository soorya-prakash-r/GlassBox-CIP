"""
glassbox_client.py
Connects to the GlassBox Gradio API deployed on Kaggle via ngrok.
URL is resolved dynamically from a GitHub Gist so it survives Kaggle restarts.
"""

from __future__ import annotations
import json
import os
import threading
import time
from typing import Optional

import requests
from dotenv import load_dotenv
from gradio_client import Client

# ── Load .env ──────────────────────────────────────────────────────────────────
load_dotenv()

GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
GIST_ID: str      = os.getenv("GIST_ID", "")

if not GITHUB_TOKEN or not GIST_ID:
    raise EnvironmentError(
        "❌ GITHUB_TOKEN or GIST_ID not set.\n"
        "Add them to your .env file:\n"
        "  GITHUB_TOKEN=your_pat_token\n"
        "  GIST_ID=your_gist_id"
    )

# ── Internal state ─────────────────────────────────────────────────────────────
_client: Optional[Client] = None
_client_lock = threading.Lock()


# ── URL resolver ───────────────────────────────────────────────────────────────
def _get_url() -> str:
    """Fetch the latest ngrok URL from GitHub Gist."""
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    r = requests.get(
        f"https://api.github.com/gists/{GIST_ID}",
        headers=headers,
        timeout=10
    )
    r.raise_for_status()
    content = r.json()["files"]["glassbox_url.json"]["content"]
    url = json.loads(content).get("url", "")
    if not url:
        raise ValueError(
            "❌ No URL found in Gist.\n"
            "Is your Kaggle session running? "
            "Start the notebook and re-run Cell 3."
        )
    return url


# ── Client factory ─────────────────────────────────────────────────────────────
def get_client(force_refresh: bool = False) -> Client:
    """Return a live Client, refreshing URL from Gist if needed."""
    global _client
    with _client_lock:
        if _client is None or force_refresh:
            url = _get_url()
            print(f"🔗 GlassBox connecting → {url}")
            _client = Client(url)
    return _client


# ── Warmup ─────────────────────────────────────────────────────────────────────
def warmup(console=None, max_attempts: int = 5, wait: int = 15) -> bool:
    """
    Pre-warm the Kaggle model session.
    The first request after idle takes ~30-60s to load the model.
    Returns True if successful.
    """
    msg = lambda s, **kw: (console.print(s, **kw) if console else print(s))

    msg("  → Waking up AI backend (may take ~60s if idle)...", style="dim" if console else "")
    for attempt in range(1, max_attempts + 1):
        try:
            client = get_client()
            client.predict("# warmup", api_name="/summarize_component")
            msg("  ✓ AI backend ready", **({"style": "green"} if console else {}))
            return True
        except Exception as e:
            err = str(e).lower()
            is_timeout = any(t in err for t in ("timed out", "timeout", "read", "connect"))
            if is_timeout and attempt < max_attempts:
                msg(f"  ⏳ Still waking up... ({attempt}/{max_attempts})",
                    **({"style": "yellow"} if console else {}))
                time.sleep(wait)
            else:
                msg(f"  ⚠ Warmup warning: {e}",
                    **({"style": "yellow"} if console else {}))
                return False
    return False


# ── Internal call with auto-retry ──────────────────────────────────────────────
def _call(api_name: str, *args):
    """
    Call the Gradio API endpoint with one automatic URL refresh on failure.
    Returns the raw tuple from client.predict().
    """
    try:
        return get_client().predict(*args, api_name=api_name)
    except Exception as e:
        print(f"⚠ Connection error ({type(e).__name__}) — refreshing URL and retrying...")
        return get_client(force_refresh=True).predict(*args, api_name=api_name)


# ── Public API ─────────────────────────────────────────────────────────────────
def get_component_summary(code_context: str) -> str:
    """
    Summarize a single code component (function / class / enum / etc.).

    Args:
        code_context: Structured code block produced by normalizer.
                      e.g. "Language: python\\nType: function\\nName: foo\\n..."

    Returns:
        Parsed summary string in format:
          "One-liner:\\n<text>\\n\\nDetailed Summary:\\n1. ...\\n2. ..."
    """
    _, parsed = _call("/summarize_component", code_context)
    return parsed


def get_file_summary(component_summaries: str, filename: str = "") -> str:
    """
    Roll up component-level one-liners into a file-level summary.

    Args:
        component_summaries: Newline-separated component one-liners.
        filename: Optional filename for context (e.g. "parser_engine.py").

    Returns:
        Parsed summary string in format:
          "File One-liner:\\n<text>"
    """
    _, parsed = _call("/summarize_file", component_summaries, filename)
    return parsed


def get_project_summary(file_summaries: str, project_name: str = "") -> str:
    """
    Roll up file-level one-liners into a project-level summary.

    Args:
        file_summaries: Newline-separated file one-liners.
        project_name:   Optional project/repo name for context.

    Returns:
        Parsed summary string in format:
          "Project One-liner:\\n<text>"
    """
    _, parsed = _call("/summarize_project", file_summaries, project_name)
    return parsed