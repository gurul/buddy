"""Optional Exa reference search; learner work and images are never sent."""
import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .. import spend


def search_problems(topic, level):
    key = os.environ.get("EXA_API_KEY", "").strip()
    if not key:
        return [], ("Exa search is off. Add EXA_API_KEY to ~/.config/cc-buddy-bridge/env and restart "
                    "to enable practice references.")
    body = {"query": f"Practice problems and exercises: {str(topic)[:200]}. Level: {str(level)[:100]}",
            "type": "auto", "numResults": 3, "moderation": True,
            "contents": {"text": {"maxCharacters": 3000}}}
    req = urllib.request.Request("https://api.exa.ai/search", json.dumps(body).encode(),
                                 {"x-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.load(response)
        # Exa's reply states its own price (costDollars.total); without one the call is recorded unpriced.
        price = data.get("costDollars") if isinstance(data, dict) else None
        cost = price.get("total") if isinstance(price, dict) else None
        spend.record("exa", "search", spend.LESSONS, cost if isinstance(cost, (int, float)) else None,
                     source="reported", note="" if isinstance(cost, (int, float)) else "no costDollars in the reply")
        rows = data.get("results") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ValueError("Invalid search response")
        sources = []
        for row in rows[:3]:
            if not isinstance(row, dict):
                continue
            url, content = row.get("url"), row.get("text")
            if not isinstance(url, str) or not isinstance(content, str) or not content.strip():
                continue
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                continue
            sources.append({"title": str(row.get("title") or parsed.hostname)[:200],
                            "url": url[:2000], "text": content[:3000]})
        return sources, ("Practice references found with Exa; Buddy creates an adapted problem."
                         if sources else "Exa found no usable references. Buddy created a problem without search references.")
    except urllib.error.HTTPError as exc:
        return [], f"Exa search failed (HTTP {exc.code}). Buddy created a problem without search references."
    except (OSError, ValueError, TypeError):
        return [], "Exa search was unavailable. Buddy created a problem without search references."
