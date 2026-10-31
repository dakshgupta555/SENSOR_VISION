"""Component identification: OCR text → candidate reference records.

Matching is deterministic and explainable: every candidate carries the token that matched,
the pattern it matched and how (exact, OCR-normalised, contained, fuzzy).
"""
import re
from difflib import SequenceMatcher

TO_DIGIT = {"O": "0", "D": "0", "Q": "0", "U": "0", "I": "1", "L": "1", "J": "1", "Z": "2", "S": "5", "B": "8", "G": "6", "T": "7", "A": "4"}
TO_ALPHA = {"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z", "6": "G", "4": "A", "7": "T"}

# Marking prefixes that identify a manufacturer on their own.
PREFIX_HINTS = [("SN74", "ti"), ("SN54", "ti"), ("ATMEGA", "microchip"), ("ATTINY", "microchip"), ("ESP32", "espressif")]


def clean(s):
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def tokenize(text):
    up = (text or "").upper()
    up = re.sub(r"[^A-Z0-9\-/.\s]", " ", up)
    raw = re.findall(r"[A-Z0-9][A-Z0-9\-/.]*", up)
    tokens = [t.strip("-/.") for t in raw if t.strip("-/.")]
    joined = []
    for a, b in zip(tokens, tokens[1:]):
        if len(a) + len(b) <= 16:
            joined.append(a + b)
    return tokens, joined


def fix_confusions(token, pattern):
    """Map look-alike characters in token towards the character class expected by pattern."""
    if len(token) != len(pattern):
        return token
    out = []
    for tc, pc in zip(token, pattern):
        if pc.isdigit() and tc in TO_DIGIT:
            out.append(TO_DIGIT[tc])
        elif pc.isalpha() and tc in TO_ALPHA:
            out.append(TO_ALPHA[tc])
        else:
            out.append(tc)
    return "".join(out)


def match_score(token, pattern):
    t, p = clean(token), clean(pattern)
    if not t or not p:
        return 0.0, None
    if t == p:
        return 1.0, "exact"
    if len(t) == len(p) and fix_confusions(t, p) == p:
        return 0.95, "ocr-normalised"
    if len(p) >= 5 and p in t:
        return 0.86 + 0.1 * len(p) / len(t), "contained"
    if len(p) >= 5 and len(t) > len(p):
        for k in range(len(t) - len(p) + 1):
            if fix_confusions(t[k:k + len(p)], p) == p:
                return 0.82 + 0.1 * len(p) / len(t), "contained, ocr-normalised"
    if len(p) >= 5:
        r = SequenceMatcher(None, t, p).ratio()
        if r >= 0.78:
            return round(0.85 * r, 4), "fuzzy"
    return 0.0, None


def manufacturer_hints(tokens, manufacturers):
    hints = {}
    for tok in tokens:
        ct = clean(tok)
        for m in manufacturers:
            for alias in m.get("aliases", []) + [m["name"]]:
                ca = clean(alias)
                if not ca:
                    continue
                if ct == ca or (len(ca) >= 5 and SequenceMatcher(None, ct, ca).ratio() >= 0.85):
                    hints.setdefault(m["id"], {"id": m["id"], "name": m["name"], "evidence": f'marking text "{tok}"'})
        for prefix, mid in PREFIX_HINTS:
            if ct.startswith(prefix) or fix_confusions(ct[:len(prefix)], prefix) == prefix:
                m = next((x for x in manufacturers if x["id"] == mid), None)
                if m:
                    hints.setdefault(mid, {"id": mid, "name": m["name"], "evidence": f'part prefix "{prefix}"'})
    return list(hints.values())


def date_codes(tokens):
    out = []
    for tok in tokens:
        c = clean(tok)
        if len(c) == 4:
            c = fix_confusions(c, "0000")
            if c.isdigit():
                yy, ww = int(c[:2]), int(c[2:])
                if 1 <= ww <= 53:
                    out.append({"code": c, "year": 2000 + yy if yy < 70 else 1900 + yy, "week": ww, "format": "YYWW (assumed)"})
    return out


def identify(text, references, manufacturers):
    tokens, joined = tokenize(text)
    all_tokens = tokens + joined
    result = {"text": text or "", "tokens": tokens, "candidates": [], "manufacturer_hints": [],
              "date_codes": date_codes(tokens), "status": "empty", "message": ""}
    if not tokens:
        result["message"] = "No legible characters to identify."
        return result
    hints = manufacturer_hints(tokens, manufacturers)
    hint_ids = {h["id"] for h in hints}
    result["manufacturer_hints"] = hints
    cands = []
    for ref in references:
        best = (0.0, None, None, None)
        for pat in ref.get("match_patterns", []):
            for tok in all_tokens:
                s, how = match_score(tok, pat)
                # Prefer longer (more specific) patterns at equal score.
                s_adj = s + 0.001 * len(pat)
                if s_adj > best[0]:
                    best = (s_adj, how, tok, pat)
        if best[0] <= 0.0:
            continue
        score = best[0] - 0.001 * len(best[3])
        if hint_ids:
            score += 0.04 if ref.get("manufacturer_id") in hint_ids else -0.06
        legacy = next((lt for lt in ref.get("legacy_types", []) if clean(lt["type"]) in {clean(t) for t in all_tokens}), None)
        cands.append({
            "reference_id": ref["id"], "part_number": ref["part_number"], "base_part": ref.get("base_part"),
            "manufacturer_id": ref.get("manufacturer_id"), "manufacturer": ref.get("manufacturer"),
            "profile_id": ref.get("profile_id"), "score": round(min(score, 1.0), 3), "match_type": best[1],
            "matched_token": best[2], "matched_pattern": best[3], "legacy_type": legacy,
        })
    cands = [c for c in cands if c["score"] >= 0.6]
    cands.sort(key=lambda c: c["score"], reverse=True)
    result["candidates"] = cands
    if not cands:
        result["status"] = "no_match"
        result["message"] = "No part number in the local reference database matches this marking."
        return result
    top = cands[0]
    rivals = [c for c in cands[1:] if c["base_part"] == top["base_part"] and c["manufacturer_id"] != top["manufacturer_id"]
              and top["score"] - c["score"] < 0.05]
    if rivals:
        result["status"] = "ambiguous"
        names = ", ".join(sorted({top["manufacturer"]} | {c["manufacturer"] for c in rivals}))
        result["message"] = f"{top['base_part']} is listed for several manufacturers ({names}). Confirm the manufacturer."
    elif top["score"] >= 0.8:
        result["status"] = "identified"
        result["message"] = f"Identified {top['part_number']} ({top['manufacturer']}) — {top['match_type']} match on \"{top['matched_token']}\"."
    else:
        result["status"] = "weak"
        result["message"] = f"Weak match to {top['part_number']} — confirm or correct the marking text."
    return result
