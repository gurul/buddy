"""Optional Exa reference search; learner work and images are never sent."""
import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlsplit


def search_problems(topic, level):
    key = os.environ.get("EXA_API_KEY", "").strip()
    if not key:
        return [], "Exa search is off. Add EXA_API_KEY to enable practice references."
    body = {"query": f"Math practice problems and exercises: {str(topic)[:200]}. Level: {str(level)[:100]}",
            "type": "auto", "numResults": 3, "moderation": True,
            "includeDomains": ["khanacademy.org", "openstax.org", "mathsisfun.com",
                               "ocw.mit.edu", "tutorial.math.lamar.edu"],
            "contents": {"text": {"maxCharacters": 3000}}}
    req = urllib.request.Request("https://api.exa.ai/search", json.dumps(body).encode(),
                                 {"x-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.load(response)
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
