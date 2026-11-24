import io
import json
import re
import unicodedata
from typing import Dict, Any, List, Optional, Tuple
from PIL import Image, ImageEnhance, ImageOps
from rapidocr_onnxruntime import RapidOCR

import db

_OCR_ENGINE: Optional[RapidOCR] = None

_NUM_RE = re.compile(r"([0-9]+(?:[.,][0-9]+)?\s*[kmbt]?)", re.IGNORECASE)
_INVOKE_MULT_RE = re.compile(r"x\s*(\d+)\b", re.IGNORECASE)
_XP_BAR_RE = re.compile(r"\d+\s*/\s*\d+\s*xp", re.IGNORECASE)
_RATIO_RE = re.compile(r"(\d{1,2})\s*/\s*(\d{1,2})\b")
_TIMER_RE = re.compile(r"\b\d+\s*[hdj]\s*\d+\s*[mh]\b|\b\d+\s*m\s*\d+\s*s\b", re.IGNORECASE)
_PCT_RE = re.compile(r"([0-9]+(?:[.,][0-9]+)?)\s*%")
_LAST_INT_RE = re.compile(r"(\d+)\s*$")

# Optional multilingual hints (FR / EN / ES / DE / PT) used only as tie-breakers on cropped screens
_PET_HINTS = ("animaux", "pets", "mascotas", "haustiere", "euf", "oeuf", "fuf", "egg", "huevo", "ovo")
_MOUNT_HINTS = ("montures", "mounts", "monturas", "reittiere", "montarias")
_FORGE_HINTS = ("forge", "forja", "schmiede", "forjar")


def _get_ocr() -> RapidOCR:
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        _OCR_ENGINE = RapidOCR()
    return _OCR_ENGINE


def _norm(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def _fix_digit_typos(token: str) -> str:
    """Fix common OCR confusions inside numeric strings (e.g. '18l032' -> '181032', '1O0' -> '100')."""
    s = re.sub(r"(?<=\d)[Oo](?=\d|[.,kmbt]|$)", "0", token)
    s = re.sub(r"(?<=^)[Oo](?=\d)", "0", s)
    s = re.sub(r"(?<=\d)[Il|](?=\d)", "1", s)
    return s


def _extract_amount(text: str) -> Optional[int]:
    """Extract a positive game amount from an OCR box (ignores ratios, timers, stages, percentages)."""
    cleaned = _fix_digit_typos(text.strip())
    if "%" in cleaned and not cleaned.startswith("%"):
        return None
    if re.search(r"\d\s*[/:-]\s*\d", cleaned) or _TIMER_RE.search(cleaned):
        return None
    cleaned = re.sub(r"^[^0-9]+", "", cleaned)
    m = _NUM_RE.match(cleaned)
    if not m:
        return None
    try:
        return db.parse_amount(m.group(1))
    except ValueError:
        return None


def _extract_player_power(items: List[Dict[str, Any]]) -> Optional[str]:
    """Extract player power (e.g. '369b') from top-left profile badge (language-independent)."""
    candidates = [
        it for it in items
        if 0.12 <= it["cx"] <= 0.44 and 0.06 <= it["cy"] <= 0.18
    ]
    candidates.sort(key=lambda it: (0 if re.search(r"[a-z]{1,2}$", it["norm"]) else 1, it["cy"]))
    for it in candidates:
        cleaned = _fix_digit_typos(re.sub(r"^[^0-9]+", "", it["text"].strip()))
        if re.fullmatch(r"[0-9]+(?:[.,][0-9]+)?\s*[a-z]{0,2}", cleaned, re.IGNORECASE):
            if cleaned.isdigit() and int(cleaned) < 100:
                continue
            try:
                return db.normalize_power(cleaned)
            except ValueError:
                continue
    return None


def _forge_level_to_tiers(forge_level: int) -> Tuple[int, int]:
    """Map Forge level to (J1 points, J3/J5 points) per hammer."""
    if forge_level >= 15:
        return 5, 4
    if forge_level >= 7:
        return 4, 3
    return 2, 1


def _find_summon_cost(items: List[Dict[str, Any]], y_min: float = 0.45, y_max: float = 0.85) -> Optional[float]:
    """
    Language-independent summon button detector:
    Finds any centered button containing 'x1' / 'x5' / 'x10' (e.g. 'Invoquer x5', 'Summon x5', 'Invocar x5')
    and reads the numeric cost directly beneath it.
    """
    for it in items:
        if not (0.35 <= it["cx"] <= 0.65 and y_min <= it["cy"] <= y_max):
            continue
        m = _INVOKE_MULT_RE.search(it["norm"])
        if not m:
            continue
        mult = max(1, int(m.group(1)))
        ix, iy = it["cx"], it["cy"]
        candidates = [
            c for c in items
            if c is not it
            and 0.005 <= (c["cy"] - iy) <= 0.09
            and abs(c["cx"] - ix) <= 0.16
        ]
        candidates.sort(key=lambda c: (c["cy"] - iy) + abs(c["cx"] - ix))
        for cand in candidates:
            amt = _extract_amount(cand["text"])
            if amt is not None and amt > 0:
                return round(amt / mult, 2)
    return None


def _detect_merge_kind_by_color(im: Optional[Image.Image], xp_y: float) -> Optional[str]:
    """
    Language-independent visual check on Merge screens:
    - Pet merge has 5 filter tiles including a pink paw tile on the right (~x=0.69) and blue egg tile (~x=0.40).
    - Mount merge has only 3 centered filter tiles (x=0.37..0.63) with pure white modal background at x=0.29 and x=0.70.
    """
    if im is None:
        return None
    w, h = im.size
    # Sample strictly inside the filter-tile row (~10%..15% below XP bar on full screens, ~35%..45% on cropped)
    y_start = min(h - 2, max(0, int(h * (xp_y + 0.10))))
    y_end = min(h - 1, max(y_start + 1, int(h * (xp_y + 0.15))))
    # Check x=0.70 (pink paw tile on pet_merge -> pinkish R > G+10 & R > B+10; white on mount_merge)
    x = int(w * 0.70)
    for y in range(y_start, y_end, max(1, (y_end - y_start) // 8)):
        r, g, b = im.getpixel((x, y))
        if r > 200 and (r - g) > 12 and (r - b) > 12:
            return "pet_merge"
    return "mount_merge"


def _preprocess_image(im: Image.Image) -> Image.Image:
    w, h = im.size
    if w < 900:
        im = im.resize((w * 2, h * 2), Image.Resampling.LANCZOS)
    return ImageEnhance.Sharpness(im).enhance(1.25)


def _ocr_to_items(im: Image.Image) -> List[Dict[str, Any]]:
    w, h = im.size
    res, _ = _get_ocr()(im)
    items: List[Dict[str, Any]] = []
    for box, txt, conf in (res or []):
        if not txt or float(conf) < 0.45:
            continue
        cx = sum(p[0] for p in box) / 4.0 / max(1, w)
        cy = sum(p[1] for p in box) / 4.0 / max(1, h)
        items.append({"text": txt, "norm": _norm(txt), "cx": cx, "cy": cy, "conf": float(conf)})
    return items


def parse_ocr_items(
    items: List[Dict[str, Any]],
    image: Optional[Image.Image] = None,
) -> Dict[str, Any]:
    """
    Classify a Forge Master screenshot independently of game language (FR/EN/ES/DE/...)
    using UI geometry, universal numeric patterns ('xp', '%', 'X/Y', 'x1/x5', timers) and visual cues.
    """
    all_norm = " ".join(it["norm"] for it in items)
    visible: Dict[str, Any] = {}
    secret: Dict[str, Any] = {}
    screen_type = "unknown"

    # 1. MERGE SCREENS — Universal signature: XP bar ('12345/67890 xp') in upper-middle + selected count below left
    xp_box = next((it for it in items if _XP_BAR_RE.search(it["norm"])), None)
    if xp_box is not None:
        xp_y = xp_box["cy"]
        # Selected count sits below the XP bar on the left half (x < 0.48)
        sel_candidates = [
            it for it in items
            if it["cx"] < 0.48 and 0.02 <= (it["cy"] - xp_y) <= 0.25
        ]
        sel_candidates.sort(key=lambda it: it["cy"] - xp_y)
        selected_amt = None
        for cand in sel_candidates:
            amt = _extract_amount(cand["text"])
            if amt is not None:
                selected_amt = amt
                break

        if selected_amt is not None:
            # Determine Pet vs Mount merge: check multilingual hints first, then visual 5-tile vs 3-tile layout
            if any(h in all_norm for h in _PET_HINTS):
                screen_type = "pet_merge"
            elif any(h in all_norm for h in _MOUNT_HINTS):
                screen_type = "mount_merge"
            else:
                screen_type = _detect_merge_kind_by_color(image, xp_y) or "mount_merge"

            if screen_type == "pet_merge":
                visible["fusion_pets"] = selected_amt
            else:
                visible["fusion_montures"] = selected_amt
            return {"screen_type": screen_type, "visible": visible, "secret": secret}

    # 2. TECH TREE — Universal signature: >= 2 percentage cards ('57.4%', '9.55%', '27.6%', '7.66%') in middle band
    pct_boxes = []
    for it in items:
        if 0.22 <= it["cy"] <= 0.65:
            pm = _PCT_RE.search(it["text"])
            if pm:
                pct_boxes.append((round(it["cy"], 2), round(it["cx"], 2), float(pm.group(1).replace(",", "."))))

    if len(pct_boxes) >= 2:
        screen_type = "tech_tree"
        # Top-left red potions (~y=0.06..0.13) & green vials directly below (~y=0.12..0.19)
        top_left_nums = []
        for it in items:
            if it["cx"] < 0.55 and 0.05 <= it["cy"] <= 0.20:
                amt = _extract_amount(it["text"])
                if amt is not None:
                    # Exclude right-side gem counter (cx > 0.60 already filtered)
                    top_left_nums.append((it["cy"], it["cx"], amt))
        top_left_nums.sort(key=lambda t: (t[0], t[1]))
        if len(top_left_nums) >= 1:
            visible["potions"] = top_left_nums[0][2]
        if len(top_left_nums) >= 2:
            visible["fioles_vertes"] = top_left_nums[1][2]
        secret["tech_tree_json"] = json.dumps([p[2] for p in sorted(pct_boxes)])
        return {"screen_type": screen_type, "visible": visible, "secret": secret}

    # 3. SKILLS — Universal signature: ratio 'X/Y' (e.g. '18/18') in top header (cy < 0.16)
    skills_hdr = next(
        (it for it in items if 0.30 <= it["cx"] <= 0.75 and it["cy"] < 0.16 and _RATIO_RE.search(it["norm"])),
        None,
    )
    if skills_hdr is not None:
        screen_type = "skills"
        hdr_y = skills_hdr["cy"]
        top_left = [
            it for it in items
            if it is not skills_hdr
            and it["cx"] < 0.38
            and abs(it["cy"] - hdr_y) <= 0.06
            and _extract_amount(it["text"]) is not None
        ]
        top_left.sort(key=lambda it: abs(it["cy"] - hdr_y))
        if top_left:
            amt = _extract_amount(top_left[0]["text"])
            if amt is not None:
                visible["competences"] = amt
        cost = _find_summon_cost(items, y_min=0.60, y_max=0.84)
        if cost is not None:
            secret["skill_summon_cost"] = cost
        return {"screen_type": screen_type, "visible": visible, "secret": secret}

    # 4. ANIMALS / PETS — Universal signature: hatch timers ('5h47m', '1h36m') or '+1' incubator slot at bottom (y=0.65..0.82)
    has_timers = any(_TIMER_RE.search(it["norm"]) and 0.65 <= it["cy"] <= 0.82 for it in items)
    has_plus_one = any(it["norm"] == "+1" and it["cx"] > 0.70 and 0.64 <= it["cy"] <= 0.78 for it in items)
    has_pet_hdr = any(it["norm"] in _PET_HINTS and it["cy"] < 0.18 for it in items)
    if has_timers or has_plus_one or has_pet_hdr:
        screen_type = "animals"
        top_left = [
            it for it in items
            if it["cx"] < 0.35
            and 0.05 <= it["cy"] <= 0.16
            and _extract_amount(it["text"]) is not None
        ]
        top_left.sort(key=lambda it: it["cy"])
        if top_left:
            amt = _extract_amount(top_left[0]["text"])
            if amt is not None:
                visible["oeufs"] = amt
        cost = _find_summon_cost(items, y_min=0.45, y_max=0.66)
        if cost is not None:
            secret["egg_summon_cost"] = cost
        return {"screen_type": screen_type, "visible": visible, "secret": secret}

    # 5. MOUNTS — Universal signature: centered modal with summon button at y=0.64..0.78 + key count centered at y=0.22..0.32
    mount_summon_cost = _find_summon_cost(items, y_min=0.64, y_max=0.78)
    has_mount_hdr = any(it["norm"] in _MOUNT_HINTS and 0.14 <= it["cy"] <= 0.34 for it in items)
    centered_key_candidates = [
        it for it in items
        if 0.38 <= it["cx"] <= 0.64
        and 0.22 <= it["cy"] <= 0.32
        and _extract_amount(it["text"]) is not None
    ]
    if has_mount_hdr or (mount_summon_cost is not None and centered_key_candidates):
        screen_type = "mounts"
        centered_key_candidates.sort(key=lambda it: abs(it["cx"] - 0.50) + abs(it["cy"] - 0.27))
        if centered_key_candidates:
            amt = _extract_amount(centered_key_candidates[0]["text"])
            if amt is not None:
                visible["cles_monture"] = amt
        if mount_summon_cost is not None:
            secret["mount_summon_cost"] = mount_summon_cost
        power = _extract_player_power(items)
        if power:
            visible["puissance"] = power
        return {"screen_type": screen_type, "visible": visible, "secret": secret}

    # 6. MAIN FORGE SCREEN — Universal signature: centered anvil hammer number (x=0.36..0.64, y=0.75..0.85)
    #    + level button to its right (x=0.62..0.86, y=0.72..0.82) + player power at top-left
    anvil_nums = [
        it for it in items
        if 0.36 <= it["cx"] <= 0.64
        and 0.75 <= it["cy"] <= 0.85
        and re.fullmatch(r"[0-9][0-9\s.,]*[kmbt]?", _fix_digit_typos(it["text"].strip()), re.IGNORECASE)
    ]
    has_forge_btn = any(
        (it["norm"] in _FORGE_HINTS or _LAST_INT_RE.search(it["norm"]))
        and 0.62 <= it["cx"] <= 0.86
        and 0.72 <= it["cy"] <= 0.82
        for it in items
    )
    if anvil_nums and has_forge_btn:
        screen_type = "main"
        power = _extract_player_power(items)
        if power:
            visible["puissance"] = power
        anvil_nums.sort(key=lambda it: abs(it["cx"] - 0.50) + abs(it["cy"] - 0.80))
        amt = _extract_amount(anvil_nums[0]["text"])
        if amt is not None:
            visible["marteaux"] = amt
        # Secret Forge level: any box with a trailing integer inside the right blue Forge button
        for it in items:
            if 0.62 <= it["cx"] <= 0.86 and 0.74 <= it["cy"] <= 0.82:
                lm = _LAST_INT_RE.search(it["norm"])
                if lm:
                    j1, j3 = _forge_level_to_tiers(int(lm.group(1)))
                    secret["forge_tier_j1"] = j1
                    secret["forge_tier_j3"] = j3
                    break
        return {"screen_type": screen_type, "visible": visible, "secret": secret}

    return {"screen_type": screen_type, "visible": visible, "secret": secret}


def scan_image_bytes(data: bytes) -> Dict[str, Any]:
    """Run local RapidOCR with upscaling + high-contrast fallback on raw image bytes."""
    raw_im = Image.open(io.BytesIO(data)).convert("RGB")
    im = _preprocess_image(raw_im)
    res = parse_ocr_items(_ocr_to_items(im), image=im)

    if res["screen_type"] == "unknown" or not res["visible"]:
        boosted = ImageOps.autocontrast(im, cutoff=1)
        boosted = ImageEnhance.Contrast(boosted).enhance(1.35)
        res2 = parse_ocr_items(_ocr_to_items(boosted), image=im)
        if res2["screen_type"] != "unknown" and len(res2["visible"]) >= len(res["visible"]):
            return res2
    return res
