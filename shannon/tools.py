"""
Shannon Browser Tools — search, read, navigate, synthesize.

Search priority:
  1. Tavily (AI-native, returns page content — best for agent synthesis)
  2. SearXNG self-hosted (privacy-first meta-search, no key needed)
  3. DuckDuckGo (fallback)

Results are returned in a format Shannon can directly synthesize into
an ETD results page rendered in the Pigeon BrowserPane.
"""

import os
import json
import urllib.request
import urllib.parse
from typing import List, Dict, Optional
from pathlib import Path

TAVILY_API_KEY  = os.environ.get("TAVILY_API_KEY", "")
SEARXNG_URL     = os.environ.get("SEARXNG_URL", "http://localhost:4040")
RESULTS_DIR     = Path.home() / ".shannon" / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Search backends
# ---------------------------------------------------------------------------

def search_tavily(query: str, max_results: int = 5) -> List[Dict]:
    """
    Tavily search — AI-native, returns actual page content.
    Best for agent synthesis. Requires TAVILY_API_KEY.
    """
    if not TAVILY_API_KEY:
        raise ValueError("TAVILY_API_KEY not set")

    payload = json.dumps({
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": "advanced",
        "max_results": max_results,
        "include_answer": True,
        "include_raw_content": False,
    }).encode()

    req = urllib.request.Request(
        "https://api.tavily.com/search",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())

    results = []
    if data.get("answer"):
        results.append({
            "type": "answer",
            "title": "Direct Answer",
            "content": data["answer"],
            "url": "",
        })
    for r in data.get("results", []):
        results.append({
            "type": "result",
            "title": r.get("title", ""),
            "content": r.get("content", ""),
            "url": r.get("url", ""),
            "score": r.get("score", 0),
        })
    return results


def search_searxng(query: str, max_results: int = 8) -> List[Dict]:
    """
    SearXNG meta-search — aggregates Google, Bing, DDG, Reddit, etc.
    No API key. Self-hosted. Privacy-first.
    """
    params = urllib.parse.urlencode({
        "q": query,
        "format": "json",
        "language": "en",
        "safesearch": "0",
    })
    url = f"{SEARXNG_URL}/search?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "Shannon/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())

    results = []
    for r in data.get("results", [])[:max_results]:
        results.append({
            "type": "result",
            "title": r.get("title", ""),
            "content": r.get("content", ""),
            "url": r.get("url", ""),
            "engine": r.get("engine", ""),
        })
    return results


def search_duckduckgo(query: str, max_results: int = 5) -> List[Dict]:
    """DuckDuckGo instant answers fallback."""
    params = urllib.parse.urlencode({"q": query, "format": "json", "no_html": "1"})
    url = f"https://api.duckduckgo.com/?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "Shannon/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())

    results = []
    if data.get("AbstractText"):
        results.append({
            "type": "answer",
            "title": data.get("Heading", "Summary"),
            "content": data["AbstractText"],
            "url": data.get("AbstractURL", ""),
        })
    for r in data.get("RelatedTopics", [])[:max_results]:
        if "Text" in r:
            results.append({
                "type": "result",
                "title": r.get("Text", "")[:80],
                "content": r.get("Text", ""),
                "url": r.get("FirstURL", ""),
            })
    return results


def search(query: str, max_results: int = 6) -> Dict:
    """
    Unified search — tries Tavily → SearXNG → DuckDuckGo.
    Returns results + metadata about which backend was used.
    """
    if TAVILY_API_KEY:
        try:
            results = search_tavily(query, max_results)
            return {"results": results, "backend": "tavily", "query": query}
        except Exception:
            pass

    try:
        results = search_searxng(query, max_results)
        return {"results": results, "backend": "searxng", "query": query}
    except Exception:
        pass

    try:
        results = search_duckduckgo(query, max_results)
        return {"results": results, "backend": "duckduckgo", "query": query}
    except Exception as e:
        return {"results": [], "backend": "none", "query": query, "error": str(e)}


def fetch_page(url: str, max_chars: int = 8000) -> str:
    """Fetch and extract readable text from a URL."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 Shannon/1.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")

        # Strip HTML tags simply
        import re
        text = re.sub(r'<script[^>]*>.*?</script>', '', raw, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:max_chars]
    except Exception as e:
        return f"Could not fetch page: {e}"


# ---------------------------------------------------------------------------
# ETD Results Page Renderer
# ---------------------------------------------------------------------------

def render_results_page(
    query: str,
    results: List[Dict],
    synthesis: str,
    backend: str,
    session_id: str = "shannon",
) -> Path:
    """
    Render Shannon's search findings as an ETD-styled HTML page.
    Saved to ~/.shannon/results/latest.html — loaded in BrowserPane.
    """
    result_cards = ""
    for r in results:
        if r.get("type") == "answer":
            result_cards += f"""
            <div class="card answer-card">
              <div class="card-type">DIRECT ANSWER</div>
              <div class="card-content">{r['content']}</div>
              {f'<a href="{r["url"]}" class="card-url" target="_blank">{r["url"][:60]}…</a>' if r.get('url') else ''}
            </div>"""
        else:
            url_display = r.get('url', '')[:70] + ('…' if len(r.get('url','')) > 70 else '')
            result_cards += f"""
            <div class="card">
              <div class="card-title">{r.get('title','')}</div>
              <div class="card-content">{r.get('content','')[:400]}</div>
              {f'<a href="{r["url"]}" class="card-url" target="_blank">{url_display}</a>' if r.get('url') else ''}
            </div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Shannon — {query}</title>
<style>
  :root {{
    --cyan: #00d4ff;
    --cyan-bright: #40e8ff;
    --amber: #f59e0b;
    --amber-bright: #fbbf24;
    --purple: #a855f7;
    --purple-bright: #c084fc;
    --purple-glow: rgba(168,85,247,0.5);
    --dark: #07071a;
    --card: #0f0f28;
    --card-hover: #141430;
    --border: rgba(0,212,255,0.45);
    --border-dim: rgba(0,212,255,0.25);
    --text: #ddeeff;
    --text-dim: #8899bb;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--dark);
    color: var(--text);
    font-family: 'Courier New', monospace;
    min-height: 100vh;
    padding: 0;
  }}

  /* Header */
  .header {{
    border-bottom: 2px solid var(--border);
    padding: 20px 32px;
    display: flex;
    align-items: center;
    gap: 16px;
    background: linear-gradient(90deg, rgba(168,85,247,0.08) 0%, rgba(0,212,255,0.04) 100%);
  }}
  .header-logo {{
    font-size: 11px;
    letter-spacing: 6px;
    color: var(--purple-bright);
    text-transform: uppercase;
    text-shadow: 0 0 14px var(--purple-glow), 0 0 28px rgba(168,85,247,0.3);
  }}
  .header-query {{
    font-size: 18px;
    color: #ffffff;
    letter-spacing: 1px;
    flex: 1;
    text-shadow: 0 0 8px rgba(255,255,255,0.3);
  }}
  .header-meta {{
    font-size: 10px;
    letter-spacing: 3px;
    color: var(--cyan);
    text-transform: uppercase;
    opacity: 0.8;
  }}

  /* Synthesis */
  .synthesis {{
    margin: 28px 32px 0;
    padding: 24px 28px;
    background: rgba(245,158,11,0.08);
    border: 2px solid rgba(245,158,11,0.6);
    border-radius: 4px;
    position: relative;
    box-shadow: 0 0 20px rgba(245,158,11,0.1);
  }}
  .synthesis::before {{
    content: 'SHANNON SYNTHESIS';
    position: absolute;
    top: -10px;
    left: 16px;
    background: var(--dark);
    padding: 0 10px;
    font-size: 9px;
    letter-spacing: 4px;
    color: var(--amber-bright);
    text-shadow: 0 0 8px rgba(245,158,11,0.6);
  }}
  .synthesis p {{
    font-size: 14px;
    line-height: 1.9;
    color: #ffe8b0;
  }}

  /* Results grid */
  .results-label {{
    margin: 28px 32px 12px;
    font-size: 9px;
    letter-spacing: 4px;
    color: var(--purple-bright);
    text-transform: uppercase;
    opacity: 0.9;
    text-shadow: 0 0 6px var(--purple-glow);
  }}
  .results {{
    padding: 0 32px 40px;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
    gap: 14px;
  }}
  .card {{
    background: var(--card);
    border: 1px solid var(--border-dim);
    border-radius: 4px;
    padding: 18px 20px;
    transition: border-color 0.2s, background 0.2s, box-shadow 0.2s;
  }}
  .card:hover {{
    border-color: var(--purple-bright);
    background: var(--card-hover);
    box-shadow: 0 0 18px rgba(168,85,247,0.2), 0 0 4px rgba(0,212,255,0.1);
  }}
  .answer-card {{
    border: 2px solid rgba(245,158,11,0.7);
    background: rgba(245,158,11,0.07);
    box-shadow: 0 0 20px rgba(245,158,11,0.08);
  }}
  .answer-card:hover {{
    border-color: var(--amber-bright);
    box-shadow: 0 0 24px rgba(245,158,11,0.18);
  }}
  .card-type {{
    font-size: 8px;
    letter-spacing: 3px;
    color: var(--amber-bright);
    margin-bottom: 8px;
    text-shadow: 0 0 6px rgba(245,158,11,0.5);
  }}
  .card-title {{
    font-size: 13px;
    color: var(--purple-bright);
    margin-bottom: 10px;
    font-weight: bold;
    letter-spacing: 0.5px;
    text-shadow: 0 0 8px var(--purple-glow);
  }}
  .card-content {{
    font-size: 12px;
    line-height: 1.8;
    color: var(--text);
    margin-bottom: 12px;
  }}
  .card-url {{
    font-size: 10px;
    color: var(--text-dim);
    text-decoration: none;
    word-break: break-all;
    border-top: 1px solid var(--border-dim);
    padding-top: 8px;
    display: block;
  }}
  .card-url:hover {{ color: var(--purple-bright); text-shadow: 0 0 6px var(--purple-glow); }}

  /* Footer */
  .footer {{
    border-top: 2px solid var(--border);
    padding: 16px 32px;
    font-size: 9px;
    letter-spacing: 3px;
    color: var(--cyan);
    text-transform: uppercase;
    display: flex;
    gap: 24px;
    background: rgba(0,212,255,0.03);
  }}
  .footer span {{ opacity: 0.7; }}
</style>
</head>
<body>
  <div class="header">
    <div class="header-logo">Shannon</div>
    <div class="header-query">"{query}"</div>
    <div class="header-meta">{backend} · {len(results)} results</div>
  </div>

  <div class="synthesis">
    <p>{synthesis.replace(chr(10), '<br>')}</p>
  </div>

  <div class="results-label">Source Results</div>
  <div class="results">
    {result_cards}
  </div>

  <div class="footer">
    <span>Shannon LTM</span>
    <span>·</span>
    <span>Pigeon Search</span>
    <span>·</span>
    <span>{backend.upper()}</span>
  </div>
</body>
</html>"""

    path = RESULTS_DIR / "latest.html"
    path.write_text(html, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Testing SearXNG...")
    data = search("Zeckendorf theorem Fibonacci")
    print(f"Backend: {data['backend']}, Results: {len(data['results'])}")
    for r in data['results'][:2]:
        print(f"  - {r['title'][:60]}")

    # Render a test page
    path = render_results_page(
        query="Zeckendorf theorem Fibonacci",
        results=data['results'],
        synthesis="The Zeckendorf theorem states that every positive integer can be uniquely represented as a sum of non-consecutive Fibonacci numbers. This property is used in Shannon's addressing system.",
        backend=data['backend'],
    )
    print(f"\nResults page: {path}")
    print(f"Open in browser: file://{path}")
