"""A deterministic stand-in for the model, used by tests only.

It reads the excerpts in the prompt and answers extractively, returning raw
JSON text, so the real JSON parsing, validation, retry and citation checks
all run exactly as they do with Gemini.
"""
from __future__ import annotations

import json
import re

from bidbrief.llm import BaseLLM

EXCERPT = re.compile(r'<excerpt file="(.*?)" page="(\d+)" section="(.*?)">\n(.*?)\n</excerpt>', re.S)
STOP = set("the a an of to and or in on for by with at from as is are be this that what which how there any "
           "much many when can must new does do".split())

FIELD_KEYS = {
    "project_name": ["invites sealed bids for"],
    "owner": ["acting through"],
    "location": ["in mapleford, massachusetts"],
    "scope": ["the work consists of"],
    "bid_due": ["sealed bids will be received"],
    "pre_bid_meeting": ["pre-bid conference will be held"],
    "completion_time": ["substantially complete the work within"],
    "bid_security": ["bid security"],
    "bonds": ["performance bond"],
}
RISK_KEYS = {
    "Schedule": (["working hours", "substantially complete", "paving season"], "Medium"),
    "Liquidated damages": (["liquidated damages of"], "High"),
    "Payment": (["retain five percent"], "Low"),
    "Bonds and insurance": (["umbrella liability"], "Medium"),
    "Site conditions": (["groundwater is expected", "petroleum compounds", "test pits to locate"], "High"),
    "Traffic": (["night between", "uniformed police"], "Medium"),
    "Testing": (["pressure tested at", "bacteriological samples"], "Medium"),
    "Responsibility shifts": (["no claim for additional compensation"], "High"),
    "Labor": (["prevailing wage"], "Low"),
}


def sentences(text: str) -> list[str]:
    flat = re.sub(r"\s+", " ", text)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", flat)
    return [p.strip() for p in parts if len(p.strip()) > 15]


def toks(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", s.lower()) if t not in STOP and len(t) > 2}


class FakeLLM(BaseLLM):
    name = "fake"
    model = "extractive-test"
    supports_images = False

    def __init__(self, invalid_first: bool = False, hallucinate: bool = False) -> None:
        super().__init__()
        self._cache_dir = None  # never cache in tests
        self.invalid_first = invalid_first
        self.hallucinate = hallucinate
        self.prompts: list[str] = []

    def _excerpts(self, prompt: str) -> list[tuple[str, int, str]]:
        out = []
        for f, p, _sec, text in EXCERPT.findall(prompt):
            for s in sentences(text):
                out.append((f, int(p), s))
        return out

    def _find(self, sents, keys):
        for key in keys:
            for f, p, s in sents:
                if key in s.lower():
                    return f, p, s
        return None

    def _call(self, system: str, prompt: str, images: list[bytes]) -> str:
        self.prompts.append(prompt)
        if self.invalid_first and "previous reply was not valid" not in prompt:
            return '{"oops": not json'
        sents = self._excerpts(prompt)
        if prompt.startswith("Fill in the bid brief"):
            fields = []
            for name, keys in FIELD_KEYS.items():
                hit = self._find(sents, keys)
                if not hit:
                    fields.append({"name": name, "value": None, "citations": []})
                    continue
                f, p, s = hit
                fields.append({"name": name, "value": s, "citations": [{"file": f, "page": p, "quote": s}]})
            return json.dumps({"fields": fields})
        if prompt.startswith("Find bidding risks"):
            cats = re.findall(r"^- ([A-Za-z ]+): ", prompt.split("Severity:")[0], re.M)
            risks = []
            for cat in cats:
                keys, sev = RISK_KEYS.get(cat, ([], "Low"))
                for key in keys:
                    hit = self._find(sents, [key])
                    if hit:
                        f, p, s = hit
                        risks.append({"category": cat, "reason": f"Check this: {s[:60]}", "severity": sev,
                                      "citations": [{"file": f, "page": p, "quote": s}]})
            if self.hallucinate:
                risks.append({"category": "Payment", "reason": "Payment within 90 days", "severity": "High",
                              "citations": [{"file": "x.pdf", "page": 1, "quote": "Payment is due within ninety days of invoice."}]})
            return json.dumps({"risks": risks})
        if prompt.startswith("List every change"):
            add_part, _, orig_part = prompt.partition("ORIGINAL EXCERPTS:")
            add = self._excerpts(add_part)
            orig = self._excerpts(orig_part)
            changes = []
            for f, p, s in add:
                low = s.lower()
                if "changed" not in low and "allowed" not in low:
                    continue
                kind, affects = "Other", "other"
                if "bid opening" in low:
                    kind, affects = "Bid date or time", "bid_due"
                elif "quantity" in low:
                    kind, affects = "Bid items or quantities", "bid_items"
                elif "work" in low:
                    kind, affects = "Schedule", "other"
                st = toks(s)
                best = max(orig, key=lambda o: len(st & toks(o[2])), default=None)
                old = [{"file": best[0], "page": best[1], "quote": best[2]}] if best and len(st & toks(best[2])) >= 3 else []
                changes.append({"addendum": "Addendum No. 1", "kind": kind, "summary": s, "affects": affects,
                                "new_citations": [{"file": f, "page": p, "quote": s}], "old_citations": old})
            return json.dumps({"changes": changes})
        if prompt.startswith("QUESTION:"):
            q = prompt.split("\n", 1)[0][len("QUESTION:"):]
            qt = toks(q)
            best, score = None, 0.0
            for f, p, s in sents:
                st = toks(s)
                sc = len(qt & st) / (len(qt) or 1)
                if sc > score:
                    best, score = (f, p, s), sc
            if best is None or score < 0.5:
                return json.dumps({"text": "The documents don't answer this.", "found": False, "citations": []})
            f, p, s = best
            if self.hallucinate:
                return json.dumps({"text": "It is $9,999 per day.", "found": True,
                                   "citations": [{"file": f, "page": p, "quote": "a sentence that is not on the page at all"}]})
            return json.dumps({"text": s, "found": True, "citations": [{"file": f, "page": p, "quote": s}]})
        return json.dumps({"rows": []})
