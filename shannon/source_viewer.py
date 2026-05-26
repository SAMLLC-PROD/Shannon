"""Shannon CaaS — HTML source viewer for memory entries.

GET /source/{entry_id}?token=AUTH_TOKEN

Renders a clean Pigeon Design System page (warm darks) showing the source
material for any memory entry: YouTube transcript with embedded video,
saved notes with metadata, or file content.
"""

import html
import json
import re
from typing import Optional

from .store import read_by_hash, _connect


# ---------------------------------------------------------------------------
# Pigeon Design System — warm darks, clean serif typography
# ---------------------------------------------------------------------------

_CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
    --bg:       #1a1814;
    --surface:  #211e1a;
    --border:   #3a3228;
    --text:     #f0e8d8;
    --muted:    #8a7d66;
    --accent:   #d4a853;
    --dim:      #6a5f52;
    --highlight:#3a2e1a;
    --tag-bg:   #2a2520;
}

body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Georgia', 'Times New Roman', serif;
    font-size: 16px;
    line-height: 1.75;
    min-height: 100vh;
}

.container { max-width: 860px; margin: 0 auto; padding: 48px 24px; }

header {
    border-bottom: 1px solid var(--border);
    padding-bottom: 24px;
    margin-bottom: 32px;
}

.wordmark {
    font-family: 'Georgia', serif;
    font-size: 11px;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 12px;
}

h1 {
    font-size: 22px;
    font-weight: 400;
    color: var(--text);
    margin-bottom: 10px;
}

.meta {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: center;
    font-size: 13px;
    color: var(--muted);
    margin-top: 10px;
}

.tag {
    background: var(--tag-bg);
    border: 1px solid var(--border);
    border-radius: 3px;
    padding: 1px 7px;
    font-size: 11px;
    color: var(--muted);
    font-family: 'Courier New', monospace;
}

.section-label {
    font-size: 11px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 10px;
}

.video-wrap {
    position: relative;
    padding-top: 56.25%;
    margin-bottom: 28px;
    border-radius: 6px;
    overflow: hidden;
    border: 1px solid var(--border);
    background: #000;
}
.video-wrap iframe {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    border: none;
}

.content {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 28px 32px;
    margin-bottom: 24px;
    white-space: pre-wrap;
    word-break: break-word;
    font-size: 15px;
    line-height: 1.85;
}

.content.yt-content {
    border-left: 3px solid var(--accent);
}

.timestamp-note {
    font-size: 13px;
    color: var(--accent);
    margin-bottom: 16px;
    font-style: italic;
}

.hash-row {
    font-family: 'Courier New', monospace;
    font-size: 12px;
    color: var(--dim);
    word-break: break-all;
    margin-bottom: 6px;
}

footer {
    border-top: 1px solid var(--border);
    padding-top: 20px;
    margin-top: 32px;
    font-size: 13px;
    color: var(--dim);
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
}

a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

code {
    font-family: 'Courier New', monospace;
    font-size: 0.9em;
    background: var(--tag-bg);
    padding: 1px 4px;
    border-radius: 2px;
    color: var(--muted);
}

.not-found {
    text-align: center;
    padding: 80px 24px;
    color: var(--muted);
}
.not-found h1 { font-size: 18px; margin-bottom: 12px; }
"""

_PAGE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} — Shannon</title>
  <style>{css}</style>
</head>
<body>
  <div class="container">
    {body}
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts_to_seconds(ts: str) -> int:
    parts = ts.split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except (ValueError, IndexError):
        pass
    return 0


def _extract_yt(body: str) -> Optional[dict]:
    for pattern in (
        r"youtube\.com/watch\?v=([A-Za-z0-9_-]{11})",
        r"youtu\.be/([A-Za-z0-9_-]{11})",
    ):
        m = re.search(pattern, body)
        if m:
            video_id = m.group(1)
            ts_m = re.search(r"\[(\d+:\d{2}(?::\d{2})?)\]", body)
            ts_str = ts_m.group(1) if ts_m else "0:00"
            return {
                "video_id": video_id,
                "timestamp_str": ts_str,
                "timestamp_sec": _ts_to_seconds(ts_str),
            }
    return None


def _tag_pills(tags: list[str]) -> str:
    return " ".join(f'<span class="tag">{html.escape(t)}</span>' for t in tags[:12])


def _detect_file_path(body: str) -> Optional[str]:
    """Detect if the entry body prominently references a file path."""
    m = re.search(r"(?:^|\n)\s*((?:/[^\s/]+){2,}(?:\.[a-z]{1,6})?)", body)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_source_page(entry_id: str, tenant_id: Optional[str] = None) -> str:
    """
    Generate full HTML page for a memory entry.
    tenant_id is used for access control — caller must validate before calling.
    """
    conn = _connect()
    row = conn.execute(
        "SELECT content_hash, created_at, session_id, tags, tier FROM entries WHERE content_hash = ?",
        (entry_id,),
    ).fetchone()
    conn.close()

    if not row:
        return _error_page("Entry not found", entry_id)

    body_text = read_by_hash(entry_id)
    if body_text is None:
        return _error_page("Content file missing", entry_id)

    tags = json.loads(row["tags"] or "[]")
    session_id = row["session_id"] or ""
    created_raw = row["created_at"] or ""
    created = (created_raw[:19].replace("T", " ") + " UTC") if created_raw else "Unknown date"
    tier = row["tier"] or 2

    is_youtube = "youtube" in tags or session_id.startswith("yt-")
    file_path = _detect_file_path(body_text) if not is_youtube else None

    # Title
    if is_youtube:
        title_raw = (
            session_id
            .removeprefix("yt-")
            .rsplit("-", 1)[0]
            .replace("-", " ")
            .title()
        )
    elif session_id:
        title_raw = session_id.replace("-", " ").title()
    else:
        title_raw = f"Memory Entry"
    title = title_raw[:80]

    tag_html = _tag_pills(tags)
    body_escaped = html.escape(body_text)

    extra = ""
    content_class = "content"

    if is_youtube:
        yt = _extract_yt(body_text)
        if yt:
            extra = f"""\
<div class="section-label">Video</div>
<div class="video-wrap">
  <iframe
    src="https://www.youtube.com/embed/{yt['video_id']}?start={yt['timestamp_sec']}&rel=0"
    allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
    allowfullscreen>
  </iframe>
</div>
<p class="timestamp-note">Transcript segment starting at {html.escape(yt['timestamp_str'])}</p>
"""
        content_class = "content yt-content"

    elif file_path:
        # Show a note about the file reference
        extra = f"""\
<div class="section-label">Referenced File</div>
<div class="hash-row">{html.escape(file_path)}</div>
<br>
"""

    tier_labels = {1: "Tier 1 — Skill / Decision", 2: "Tier 2 — General", 3: "Tier 3 — Raw / Transcript"}
    tier_label = tier_labels.get(tier, "Unknown tier")

    inner = f"""\
<header>
  <div class="wordmark">Shannon Memory</div>
  <h1>{html.escape(title)}</h1>
  <div class="meta">
    <span>{html.escape(created)}</span>
    <span>{html.escape(tier_label)}</span>
    {tag_html}
  </div>
</header>

{extra}

<div class="section-label">Content</div>
<div class="{content_class}">{body_escaped}</div>

<footer>
  <div class="hash-row">ID: <code>{html.escape(entry_id)}</code></div>
  <a href="https://shannon.latticeproxy.io">Shannon Memory</a>
</footer>"""

    return _PAGE.format(title=html.escape(title), css=_CSS, body=inner)


def render_source_page_for_internal(entry_id: str) -> str:
    """Render source page without tenant auth check (internal/admin use)."""
    return render_source_page(entry_id, tenant_id=None)


# ---------------------------------------------------------------------------
# Error pages
# ---------------------------------------------------------------------------

def _error_page(message: str, entry_id: str = "") -> str:
    inner = f"""\
<header>
  <div class="wordmark">Shannon Memory</div>
  <h1>Not Found</h1>
</header>
<div class="not-found">
  <p style="color:var(--muted)">{html.escape(message)}</p>
  {f'<p class="hash-row" style="margin-top:12px">ID: <code>{html.escape(entry_id[:32])}</code></p>' if entry_id else ""}
</div>"""
    return _PAGE.format(title="Not Found — Shannon", css=_CSS, body=inner)


def auth_error_page() -> str:
    inner = """\
<header>
  <div class="wordmark">Shannon Memory</div>
  <h1>Access Denied</h1>
</header>
<div class="not-found">
  <p style="color:var(--muted)">
    Valid authentication token required to view this entry.
  </p>
  <p style="margin-top:16px;font-size:13px;color:var(--dim)">
    Append <code>?token=YOUR_TOKEN</code> to the URL.
  </p>
</div>"""
    return _PAGE.format(title="Access Denied — Shannon", css=_CSS, body=inner)
