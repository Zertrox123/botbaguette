import io
import json
import re
import unicodedata
from typing import Dict, Any, List, Optional, Tuple
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

import db

_OCR_ENGINE: Optional[RapidOCR] = None

_NUM_RE = re.compile(r"([0-9]+(?:[.,][0-9]+)?\s*[kmbt]?)", re.IGNORECASE)
_INVOKE_MULT_RE = re.compile(r"invoquer\s*x?\s*(\d+)", re.IGNORECASE)
_SELECTED_RE = re.compile(r"([0-9][0-9\s.,]*[kmbt]?)\s*selectionn", re.IGNORECASE)
_LEVEL_RE = re.compile(r"niveau\s*(\d+)", re.IGNORECASE)
_PCT_RE = re.compile(r"([0-9]+(?:[.,][0-9]+)?)\s*%")


def _get_ocr() -> RapidOCR:
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        _OCR_ENGINE = RapidOCR()
    return _OCR_ENGINE


def _norm(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def _extract_amount(text: str) -> Optional[int]:
    # Strip leading noise symbols like '%48' or '￥369b'
    cleaned = re.sub(r"^[^0-9]+", "", text.strip())
    m = _NUM_RE.match(cleaned)
    if not m:
        return None
    try:
        return db.parse_amount(m.group(1))
    except ValueError:
        return None


def _forge_level_to_tiers(forge_level: int) -> Tuple[int, int]:
    """Map Forge level to (J1 points, J3/J5 points) per hammer."""
    if forge_level >= 15:
        return 5, 4
    if forge_level >= 7:
        return 4, 3
    return 2, 1


def _find_summon_cost(items: List[Dict[str, Any]]) -> Optional[float]:
    """Find 'Invoquer xN' and the numeric cost directly beneath it, returning cost per 1 summon."""
    for it in items:
        m = _INVOKE_MULT_RE.search(it["norm"])
        if not m:
            continue
        mult = max(1, int(m.group(1)))
        ix, iy = it["cx"], it["cy"]
        candidates = [
            c for c in items
            if 0.005 <= (c["cy"] - iy) <= 0.08 and abs(c["cx"] - ix) <= 0.14
        ]
        candidates.sort(key=lambda c: (c["cy"] - iy) + abs(c["cx"] - ix))
        for cand in candidates:
            amt = _extract_amount(cand["text"])
            if amt is not None and amt > 0:
                return round(amt / mult, 2)
    return None


def parse_ocr_items(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Classify a Forge Master screenshot from normalized OCR boxes and extract visible + secret stats."""
    all_norm = " ".join(it["norm"] for it in items)
    visible: Dict[str, Any] = {}
    secret: Dict[str, Any] = {}
    screen_type = "unknown"

    # 1. Merge screens ("697 Sélectionné" / "949 Sélectionné" + "Améliorer")
    selected_amt = None
    for it in items:
        m = _SELECTED_RE.search(it["norm"])
        if m:
            try:
                selected_amt = db.parse_amount(m.group(1))
            except ValueError:
                pass
            break

    if selected_amt is not None and "ameliorer" in all_norm:
        is_pet = (
            "animaux" in all_norm
            or any(it["norm"] in ("euf", "oeuf", "fuf") for it in items)
        )
        if is_pet:
            screen_type = "pet_merge"
            visible["fusion_pets"] = selected_amt
        else:
            screen_type = "mount_merge"
            visible["fusion_montures"] = selected_amt
        return {"screen_type": screen_type, "visible": visible, "secret": secret}

    # 2. Tech Tree ("Arbre Technologique" at top — may merge with left potion count like '75.1kArbreTechnologiqu')
    tech_hdr = next(
        (it for it in items if "arbretechnologiq" in it["norm"].replace(" ", "") and it["cy"] < 0.25),
        None,
    )
    if tech_hdr is not None:
        screen_type = "tech_tree"
        # Check if potion count was merged into the header text itself (e.g. '75.1kArbreTechnologiqu')
        hdr_amt = _extract_amount(tech_hdr["text"])
        top_left_nums = [
            it for it in items
            if it is not tech_hdr
            and it["cx"] < 0.35 and 0.05 <= it["cy"] <= 0.20
            and _extract_amount(it["text"]) is not None
        ]
        top_left_nums.sort(key=lambda it: it["cy"])
        if hdr_amt is not None:
            visible["potions"] = hdr_amt
            if len(top_left_nums) >= 1:
                amt = _extract_amount(top_left_nums[0]["text"])
                if amt is not None:
                    visible["fioles_vertes"] = amt
        else:
            if len(top_left_nums) >= 1:
                amt = _extract_amount(top_left_nums[0]["text"])
                if amt is not None:
                    visible["potions"] = amt
            if len(top_left_nums) >= 2:
                amt = _extract_amount(top_left_nums[1]["text"])
                if amt is not None:
                    visible["fioles_vertes"] = amt

        # Secret tech tree percentages
        pcts = []
        for it in items:
            if 0.25 <= it["cy"] <= 0.60:
                pm = _PCT_RE.search(it["text"])
                if pm:
                    pcts.append((round(it["cy"], 2), round(it["cx"], 2), float(pm.group(1).replace(",", "."))))
        if pcts:
            secret["tech_tree_json"] = json.dumps([p[2] for p in sorted(pcts)])
        return {"screen_type": screen_type, "visible": visible, "secret": secret}

    # 3. Skills ("Compétences 18/18" at top)
    if any("competences" in it["norm"] and it["cy"] < 0.20 for it in items):
        screen_type = "skills"
        top_left = [
            it for it in items
            if it["cx"] < 0.35 and 0.05 <= it["cy"] <= 0.16 and _extract_amount(it["text"]) is not None
        ]
        if top_left:
            amt = _extract_amount(top_left[0]["text"])
            if amt is not None:
                visible["competences"] = amt
        cost = _find_summon_cost(items)
        if cost is not None:
            secret["skill_summon_cost"] = cost
        return {"screen_type": screen_type, "visible": visible, "secret": secret}

    # 4. Animals / Eggs ("Animaux" at top)
    if any(it["norm"] == "animaux" and it["cy"] < 0.20 for it in items):
        screen_type = "animals"
        top_left = [
            it for it in items
            if it["cx"] < 0.35 and 0.05 <= it["cy"] <= 0.16 and _extract_amount(it["text"]) is not None
        ]
        if top_left:
            amt = _extract_amount(top_left[0]["text"])
            if amt is not None:
                visible["oeufs"] = amt
        cost = _find_summon_cost(items)
        if cost is not None:
            secret["egg_summon_cost"] = cost
        return {"screen_type": screen_type, "visible": visible, "secret": secret}

    # 5. Mounts ("Montures" modal)
    mount_hdr = next((it for it in items if it["norm"] == "montures" and 0.15 <= it["cy"] <= 0.35), None)
    if mount_hdr is not None:
        screen_type = "mounts"
        below = [
            it for it in items
            if 0.01 <= (it["cy"] - mount_hdr["cy"]) <= 0.08
            and abs(it["cx"] - mount_hdr["cx"]) <= 0.20
            and _extract_amount(it["text"]) is not None
        ]
        if below:
            amt = _extract_amount(below[0]["text"])
            if amt is not None:
                visible["cles_monture"] = amt
        cost = _find_summon_cost(items)
        if cost is not None:
            secret["mount_summon_cost"] = cost
        return {"screen_type": screen_type, "visible": visible, "secret": secret}

    # 6. Main Forge screen ("Forge" + "Niveau XX" + hammers on anvil)
    forge_hdr = next((it for it in items if it["norm"] == "forge" and 0.65 <= it["cy"] <= 0.85), None)
    if forge_hdr is not None:
        screen_type = "main"
        # Hammers count sits near center (x ~ 0.35..0.65, y ~ 0.74..0.85)
        anvil_nums = [
            it for it in items
            if 0.35 <= it["cx"] <= 0.65 and 0.74 <= it["cy"] <= 0.85
            and re.fullmatch(r"[0-9][0-9\s.,]*[kmbt]?", it["text"].strip(), re.IGNORECASE)
        ]
        if anvil_nums:
            amt = _extract_amount(anvil_nums[0]["text"])
            if amt is not None:
                visible["marteaux"] = amt
        # Secret Forge level
        for it in items:
            if 0.60 <= it["cx"] <= 0.90 and 0.72 <= it["cy"] <= 0.84:
                lm = _LEVEL_RE.search(it["norm"])
                if lm:
                    j1, j3 = _forge_level_to_tiers(int(lm.group(1)))
                    secret["forge_tier_j1"] = j1
                    secret["forge_tier_j3"] = j3
                    break
        return {"screen_type": screen_type, "visible": visible, "secret": secret}

    return {"screen_type": screen_type, "visible": visible, "secret": secret}


def scan_image_bytes(data: bytes) -> Dict[str, Any]:
    """Run local RapidOCR on raw image bytes and extract Forge Master stats."""
    im = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = im.size
    res, _ = _get_ocr()(im)
    items: List[Dict[str, Any]] = []
    for box, txt, conf in (res or []):
        if not txt:
            continue
        cx = sum(p[0] for p in box) / 4.0 / max(1, w)
        cy = sum(p[1] for p in box) / 4.0 / max(1, h)
        items.append({"text": txt, "norm": _norm(txt), "cx": cx, "cy": cy, "conf": float(conf)})
    return parse_ocr_items(items)
