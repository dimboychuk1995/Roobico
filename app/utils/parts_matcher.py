"""
Fuzzy matcher: handwritten part token -> shop catalog parts.

Pure Python + rapidfuzz (C extension). Designed to run in milliseconds even
against catalogs with tens of thousands of parts.

Usage:
    from app.utils.parts_matcher import build_index, match_part

    idx = build_index(parts_collection.find({"shop_id": ..., "is_active": True},
                                            {"part_number": 1, "description": 1}))
    candidates = match_part("78-120P-01", "Brake disc", idx, limit=8)
    # -> list of dicts with _id, score, reason
"""
from __future__ import annotations

import re
from typing import Iterable, Any

from rapidfuzz import fuzz, process, utils as rf_utils


# Confusable character map for handwriting (digits + letters that look alike).
# Used to "soften" each character before scoring.
_CONFUSABLE_GROUPS = [
    set("0OQD"),
    set("1IL7T"),
    set("2Z"),
    set("38B"),
    set("49AG"),
    set("5S"),
    set("6G0C"),
    set("78"),
    set("8BP3"),
    set("MN"),
    set("UV"),
]


def _build_canon_map() -> dict[str, str]:
    """For each char, pick a single canonical representative of its group."""
    canon: dict[str, str] = {}
    for group in _CONFUSABLE_GROUPS:
        rep = sorted(group)[0]
        for c in group:
            # If a char belongs to several groups (e.g. 'G' in 49AG and 6G0C),
            # the first registration wins. That's fine for fuzzy ranking.
            canon.setdefault(c, rep)
    return canon


_CANON = _build_canon_map()


def _norm_pn(s: str) -> str:
    """Uppercase, strip non-alphanumeric, then collapse confusables."""
    s = re.sub(r"[^A-Za-z0-9]", "", (s or "")).upper()
    return "".join(_CANON.get(c, c) for c in s)


def _norm_desc(s: str) -> str:
    return rf_utils.default_process(s or "")


def build_index(parts_iter: Iterable[dict]) -> dict[str, Any]:
    """
    Build an in-memory index from a Mongo cursor (or any iterable of part docs).

    Each element must have at least: _id, part_number, description.
    Returns dict with:
      docs:       list of original docs in stable order
      pn_norms:   list[str] (parallel to docs) — normalized PN
      desc_norms: list[str] (parallel to docs) — normalized description
    """
    docs: list[dict] = []
    pn_norms: list[str] = []
    desc_norms: list[str] = []
    for d in parts_iter:
        docs.append(d)
        pn_norms.append(_norm_pn(d.get("part_number") or ""))
        desc_norms.append(_norm_desc(d.get("description") or ""))
    return {"docs": docs, "pn_norms": pn_norms, "desc_norms": desc_norms}


def _pn_score_one(written_norm: str, db_norm: str) -> tuple[int, str]:
    """
    Return (score 0..100, short reason) comparing normalized PNs.
    Uses several rapidfuzz metrics and picks the strongest signal.
    """
    if not written_norm or not db_norm:
        return 0, ""

    if written_norm == db_norm:
        return 100, "exact PN"
    if len(written_norm) < 4 or len(db_norm) < 4:
        # Too short to trust any partial match.
        return 0, ""
    if db_norm.endswith(written_norm) and len(written_norm) >= 4:
        return 96, "PN suffix"
    if db_norm.startswith(written_norm) and len(written_norm) >= 4:
        return 92, "PN prefix"
    if written_norm in db_norm and len(written_norm) >= 4:
        return 88, "PN substring"
    if db_norm in written_norm and len(db_norm) >= 4:
        return 80, "DB PN inside written"

    # Levenshtein-style ratio (0..100).
    r1 = fuzz.ratio(written_norm, db_norm)
    r2 = fuzz.partial_ratio(written_norm, db_norm)
    if r2 > r1:
        return int(r2), "PN partial"
    return int(r1), "PN ratio"


def match_part(
    written_pn: str,
    written_desc: str,
    index: dict[str, Any],
    limit: int = 8,
    min_score: int = 50,
) -> list[dict]:
    """
    Score every catalog part against the written PN + description and return
    the top `limit` candidates above `min_score`.

    Returns a list of dicts:
      {"doc": <original part doc>, "score": int, "reason": str}
    """
    docs: list[dict] = index["docs"]
    pn_norms: list[str] = index["pn_norms"]
    desc_norms: list[str] = index["desc_norms"]
    if not docs:
        return []

    w_pn = _norm_pn(written_pn or "")
    w_desc = _norm_desc(written_desc or "")

    # Pre-rank candidates so we can score only a manageable subset for
    # the "expensive" combined scoring. We use rapidfuzz process.extract
    # which is C-fast even on 50k entries.
    pn_pool: list[tuple[int, int]] = []  # [(idx, pn_score)]
    if w_pn:
        # process.extract returns (choice, score, idx)
        pn_hits = process.extract(
            w_pn,
            pn_norms,
            scorer=fuzz.ratio,
            limit=max(limit * 6, 30),
            score_cutoff=40,
        )
        pn_pool = [(idx, int(score)) for (_choice, score, idx) in pn_hits]

    desc_pool: list[tuple[int, int]] = []
    if w_desc:
        desc_hits = process.extract(
            w_desc,
            desc_norms,
            scorer=fuzz.token_set_ratio,
            limit=max(limit * 6, 30),
            score_cutoff=50,
        )
        desc_pool = [(idx, int(score)) for (_choice, score, idx) in desc_hits]

    # Combine candidate sets.
    cand_idxs = {i for i, _ in pn_pool} | {i for i, _ in desc_pool}
    if not cand_idxs:
        return []

    pn_score_lookup = dict(pn_pool)
    desc_score_lookup = dict(desc_pool)

    scored: list[tuple[int, str, dict]] = []
    for i in cand_idxs:
        db_pn = pn_norms[i]
        db_desc = desc_norms[i]

        pn_sc, pn_reason = (0, "")
        if w_pn and db_pn:
            pn_sc, pn_reason = _pn_score_one(w_pn, db_pn)

        desc_sc = desc_score_lookup.get(i, 0) if w_desc else 0

        # Combined score: PN dominates when present, description tops up.
        if w_pn and pn_sc:
            total = pn_sc + min(15, desc_sc // 6)
            reason = pn_reason
            if desc_sc >= 70:
                reason += "; desc match"
        elif w_desc and desc_sc:
            total = desc_sc
            reason = "desc match"
        else:
            continue

        if total < min_score:
            continue
        scored.append((min(100, int(total)), reason, docs[i]))

    scored.sort(key=lambda x: -x[0])
    return [
        {"doc": d, "score": sc, "reason": reason}
        for (sc, reason, d) in scored[:limit]
    ]
