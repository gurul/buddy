"""Optional practice references through Firecrawl search; learner work and images are never sent.

Only the topic and level leave the Mac. One Firecrawl search (web_reader.firecrawl_search, through the watcher's
guarded http_request) reads the top three pages as markdown; the tutor uses them as inspiration for an original,
adapted problem. Exa did this until 2026-09-26, with its content moderation on; Firecrawl's search has no such
option (docs.firecrawl.dev, 2026-09-26), so the tutor prompt, which adapts rather than copies, is the filter now.
"""
import os
from urllib.parse import urlsplit

from .. import spend, watch

RESULTS = 3
TEXT_CHARS = 3000


def search_problems(topic, level):
    if not os.environ.get("FIRECRAWL_API_KEY", "").strip():
        return [], ("Firecrawl search is off. Add FIRECRAWL_API_KEY to ~/.config/cc-buddy-bridge/env and restart "
                    "to enable practice references.")
    from ..web_reader import firecrawl_search

    query = f"Practice problems and exercises: {str(topic)[:200]}. Level: {str(level)[:100]}"
    try:
        found = firecrawl_search(query, limit=RESULTS, timeout=20.0)
    except watch.FetchError as exc:
        why = f" (HTTP {exc.status})" if exc.status else ""
        return [], f"Firecrawl search failed{why}. Buddy created a problem without search references."
    except (OSError, ValueError, TypeError):
        return [], "Firecrawl search was unavailable. Buddy created a problem without search references."
    credits = found.get("credits")
    usd = watch._price_per_credit(os.environ)
    spend.record("firecrawl", "search", spend.LESSONS, credits * usd if credits and usd is not None else None,
                 note=f"{credits if credits is not None else '?'} credits")
    sources = []
    for page in found.get("pages") or []:
        url, content = page.get("url"), page.get("markdown")
        if not isinstance(url, str) or not isinstance(content, str) or not content.strip():
            continue
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            continue
        sources.append({"title": str(page.get("title") or parsed.hostname)[:200],
                        "url": url[:2000], "text": content[:TEXT_CHARS]})
    return sources, ("Practice references found with Firecrawl; Buddy creates an adapted problem."
                     if sources else "Firecrawl found no usable references. Buddy created a problem without "
                                     "search references.")
