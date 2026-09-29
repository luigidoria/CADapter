"""
recipes.py -- search over the curated recipes.
"""

from __future__ import annotations

import re
from pathlib import Path

_WORD = re.compile(r"[a-z0-9_]{2,}")
_STOP = {"the", "and", "for", "with", "how", "use", "using",
         "not", "you", "your", "from", "this", "that", "are", "can"}


def _tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP]


class RecipeIndex:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.docs: list[dict] = []
        self.reload()

    def reload(self) -> int:
        self.docs.clear()
        for path in sorted(self.folder.glob("*.md")):
            raw = path.read_text(encoding="utf-8", errors="replace")
            lines = raw.splitlines()
            title = lines[0].lstrip("# ").strip() if lines else path.stem
            keywords = ""
            for line in lines[1:6]:
                if line.lower().startswith("keywords:"):
                    keywords = line.split(":", 1)[1].strip()
                    break
            self.docs.append({
                "file": path.name,
                "title": title,
                "keywords": keywords,
                "body": raw,
                "_t_title": set(_tokens(title)),
                "_t_kw": set(_tokens(keywords)),
                "_t_body": _tokens(raw),
            })
        return len(self.docs)

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        q = _tokens(query)
        if not q:
            return []
        scored = []
        for d in self.docs:
            body_counts = {}
            for w in d["_t_body"]:
                body_counts[w] = body_counts.get(w, 0) + 1
            score = 0.0
            for w in q:
                if w in d["_t_title"]:
                    score += 6.0
                if w in d["_t_kw"]:
                    score += 4.0
                score += min(body_counts.get(w, 0), 5) * 0.5
            if score > 0:
                scored.append((score, d))
        scored.sort(key=lambda x: -x[0])
        return [{
            "title": d["title"],
            "file": d["file"],
            "score": round(s, 1),
            "recipe": d["body"][:1600],
        } for s, d in scored[:top_k]]
