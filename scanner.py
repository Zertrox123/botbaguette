import difflib
import io
import json
import re
import unicodedata
from typing import Dict, Any, List, Optional, Tuple
from PIL import Image, ImageDraw, ImageEnhance, ImageOps
from rapidocr_onnxruntime import RapidOCR

import db
import war_points

_OCR_ENGINE: Optional[RapidOCR] = None

_NUM_RE = re.compile(r"([0-9]+(?:[.,][0-9]+)?\s*[kmbt]?)", re.IGNORECASE)
_INVOKE_MULT_RE = re.compile(r"x\s*(\d+)\b", re.IGNORECASE)
_XP_BAR_RE = re.compile(r"\d+\s*/\s*\d+\s*xp", re.IGNORECASE)
_RATIO_RE = re.compile(r"(\d{1,2})\s*/\s*(\d{1,2})\b")
_TIMER_RE = re.compile(r"\b\d+\s*[hdj]\s*\d+\s*[mh]\b|\b\d+\s*m\s*\d+\s*s\b", re.IGNORECASE)
_PCT_RE = re.compile(r"([0-9]+(?:[.,][0-9]+)?)\s*%")
_LAST_INT_RE = re.compile(r"(\d+)\s*$")
_MAX_HINTS = ("maximis", "maxed", "maximo", "maxim", "max")

# Optional multilingual hints (FR / EN / ES / DE / PT) used only as tie-breakers on cropped screens
_PET_HINTS = ("animaux", "pets", "mascotas", "haustiere", "euf", "oeuf", "fuf", "egg", "huevo", "ovo")
_MOUNT_HINTS = ("montures", "mounts", "monturas", "reittiere", "montarias")
_FORGE_HINTS = ("forge", "forja", "schmiede", "forjar")


def _get_ocr() -> RapidOCR:
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        # Disable 180-degree rotation classifier so symmetric numbers ('90,700') never flip upside-down
        _OCR_ENGINE = RapidOCR(use_cls=False)
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


def _classify_rarity_rgb(rgb: Tuple[int, int, int]) -> str:
    """Classify a Forge Master pet/egg filter tile background color into a rarity key."""
    r, g, b = rgb
    spread = max(r, g, b) - min(r, g, b)
    if spread <= 9:
        return "commun"
    # Yellow (Légendaire): high R & G, lower B
    if r > b + 12 and g > b + 12 and abs(r - g) <= 14:
        return "legendaire"
    # Purple / Violet (Mythique): R & B higher than G
    if r > g + 10 and b > g + 10:
        return "mythique"
    # Pink / Red (Ultime / Héroïque): R dominates both G and B
    if r > g + 12 and r > b + 12:
        return "heroique" if (r - g) > 45 else "ultime"
    # Green (Épique): G dominates R and B
    if g > r + 8 and g > b + 8:
        return "epique"
    # Blue (Rare): B dominates both R and G
    if b > r + 10 and b >= g:
        return "rare"
    # Cyan / Teal (Infini): strong G & B, very low R
    if g > r + 25 and b > r + 25:
        return "infini"
    return "commun"


def _extract_merge_rarities(
    im: Optional[Image.Image],
    xp_y: float,
    total_selected: int,
) -> Dict[str, int]:
    """
    Extract per-rarity pet or mount counts from the filter tile row below the XP bar on merge screens.
    Language-independent: finds the tile row dynamically below xp_y, classifies tile RGB colors,
    runs circular-ring-masked badge OCR, and reconciles against total_selected.
    """
    if im is None:
        return {}
    w, h = im.size

    # Dynamically locate the filter tile row below the grey top card (white modal bg with 2..6 colored tiles)
    spans: List[Tuple[int, int]] = []
    y_mid = 0
    for offset in (0.122, 0.39, 0.14, 0.35):
        cand_y = int(h * (xp_y + offset))
        if not (0 <= cand_y < h):
            continue
        # Ensure we're inside the white modal area (left margin at x=0.10..0.22 should be near-white)
        if min(im.getpixel((int(w * 0.16), cand_y))) < 238:
            continue
        row_spans: List[Tuple[int, int]] = []
        in_tile = False
        start = 0
        for x in range(int(w * 0.16), int(w * 0.84)):
            r, g, b = im.getpixel((x, cand_y))
            is_bg = (r > 246 and g > 246 and b > 246)
            if not is_bg and not in_tile:
                in_tile, start = True, x
            elif is_bg and in_tile:
                in_tile = False
                if int(w * 0.05) <= (x - start) <= int(w * 0.15):
                    row_spans.append((start, x))
        if 1 <= len(row_spans) <= 7:
            spans = row_spans
            y_mid = cand_y
            break

    if not spans:
        return {}

    parsed: List[Tuple[str, Optional[int], float]] = []
    for x0, x1 in spans:
        tw = x1 - x0
        cx_t = (x0 + x1) // 2
        y_top = y_mid
        while y_top > 0 and min(im.getpixel((cx_t, y_top))) < 246:
            y_top -= 1
        y_bot = y_mid
        while y_bot < h - 1 and min(im.getpixel((cx_t, y_bot))) < 246:
            y_bot += 1
        cy_sample = min(h - 1, y_top + max(3, (y_bot - y_top) // 6))
        rarity = _classify_rarity_rgb(im.getpixel((cx_t, cy_sample)))

        bcx = int(x1 - tw * 0.01)
        bcy = int(y_bot - 1)
        rad = max(7, int(tw * 0.25))
        crop = im.crop((max(0, bcx - rad), max(0, bcy - rad), min(w, bcx + rad), min(h, bcy + rad)))
        big = crop.resize((120, 120), Image.Resampling.LANCZOS)
        mask = Image.new("L", (120, 120), 0)
        ImageDraw.Draw(mask).ellipse((12, 12, 108, 108), fill=255)
        clean = Image.new("RGB", (160, 160), (255, 255, 255))
        clean.paste(big, (20, 20), mask)
        clean = ImageEnhance.Contrast(clean).enhance(1.5)

        res, _ = _get_ocr()(clean)
        val: Optional[int] = None
        best_conf = 0.0
        for _, txt, conf in (res or []):
            t = _fix_digit_typos(txt.strip())
            if t.isdigit() and float(conf) >= 0.60:
                val = int(t)
                best_conf = float(conf)
                break
        parsed.append((rarity, val, best_conf))

    # Reconcile against total_selected:
    # Case A: exactly 1 tile unreadable -> fill with remainder
    missing_idxs = [i for i, (_, v, _) in enumerate(parsed) if v is None]
    known_sum = sum(v for _, v, _ in parsed if v is not None)
    if len(missing_idxs) == 1 and total_selected >= known_sum:
        idx = missing_idxs[0]
        rem = total_selected - known_sum
        parsed[idx] = (parsed[idx][0], rem, 1.0)
    # Case B: all tiles read as digits, but sum != total_selected -> fix the lowest-confidence tile
    elif not missing_idxs and total_selected > 0 and known_sum != total_selected:
        worst_idx = min(range(len(parsed)), key=lambda i: parsed[i][2])
        other_sum = known_sum - (parsed[worst_idx][1] or 0)
        rem = total_selected - other_sum
        if rem > 0:
            parsed[worst_idx] = (parsed[worst_idx][0], rem, 1.0)

    result: Dict[str, int] = {}
    for rarity, val, _ in parsed:
        if val is not None and val > 0:
            result[rarity] = result.get(rarity, 0) + val
    return result


def _detect_summon_maxed(items: List[Dict[str, Any]], y_min: float, y_max: float) -> Optional[int]:
    """
    Check the summon-level badge to the right of the summon button (x ~ 0.66..0.94, y_min..y_max).
    Returns 1 if 'Maximisé'/'Max'/lvl 100 is detected, 0 if a level < 100 is detected, else None.
    """
    right_boxes = [
        it for it in items
        if 0.66 <= it["cx"] <= 0.94 and y_min <= it["cy"] <= y_max
    ]
    for it in right_boxes:
        n = it["norm"]
        if any(h in n for h in _MAX_HINTS):
            return 1
        lm = _LAST_INT_RE.search(n)
        if lm and "/" not in n and "+" not in n:
            val = int(lm.group(1))
            if 1 <= val <= 100:
                return 1 if val >= 100 else 0
    return None


def _forge_level_to_tiers(forge_level: int) -> Tuple[int, int]:
    """Map Forge level (0..35) to integer (J1, J3/J5) fallback points per hammer."""
    avg_j1, avg_j3 = war_points.forge_level_to_avg_points(forge_level)
    return int(round(avg_j1)), int(round(avg_j3))


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
                rarities = _extract_merge_rarities(image, xp_y, selected_amt)
                if rarities:
                    secret["egg_rarity_json"] = json.dumps(rarities)
            else:
                visible["fusion_montures"] = selected_amt
                rarities = _extract_merge_rarities(image, xp_y, selected_amt)
                if rarities:
                    secret["mount_rarity_json"] = json.dumps(rarities)
            return {"screen_type": screen_type, "visible": visible, "secret": secret}

    # 2. FORGE PROBABILITIES MODAL — Universal signature: >= 8 percentage boxes in two right columns (current & next level)
    #    and two level headers side-by-side at y ~ 0.22..0.28 ('Niveau 32' | 'Niveau 33', works in FR/EN/ES/DE)
    forge_prob_cols = [
        (it["cy"], it["cx"], float(_PCT_RE.search(it["text"]).group(1).replace(",", ".")))
        for it in items
        if 0.25 <= it["cy"] <= 0.68 and 0.55 <= it["cx"] <= 0.96 and _PCT_RE.search(it["text"])
    ]
    if len(forge_prob_cols) >= 8:
        screen_type = "forge_info"
        power = _extract_player_power(items)
        if power:
            visible["puissance"] = power
        # Current Forge level sits at x ~ 0.50..0.75, y ~ 0.22..0.28 (0..35)
        lvl_headers = [
            it for it in items
            if 0.50 <= it["cx"] <= 0.76 and 0.22 <= it["cy"] <= 0.28 and _LAST_INT_RE.search(it["norm"])
        ]
        if lvl_headers:
            lvl = max(0, min(35, int(_LAST_INT_RE.search(lvl_headers[0]["norm"]).group(1))))
            secret["forge_level"] = lvl
            secret["max_forge"] = 1 if lvl >= 35 else 0
        # Current-level probability column sits at x ~ 0.55..0.76, sorted top-to-bottom (rows 0..2=+2/+1, 3..5=+4/+3, 6+=+5/+4)
        cur_col = sorted([p for p in forge_prob_cols if p[1] <= 0.76], key=lambda p: p[0])
        total_pct = sum(p[2] for p in cur_col)
        if total_pct > 0:
            w_j1 = 0.0
            w_j3 = 0.0
            for idx, (_, _, pct) in enumerate(cur_col):
                p_j1, p_j3 = (2, 1) if idx < 3 else ((4, 3) if idx < 6 else (5, 4))
                w_j1 += pct * p_j1
                w_j3 += pct * p_j3
            secret["forge_avg_j1"] = round(w_j1 / total_pct, 2)
            secret["forge_avg_j3"] = round(w_j3 / total_pct, 2)
            secret["forge_tier_j1"] = int(round(secret["forge_avg_j1"]))
            secret["forge_tier_j3"] = int(round(secret["forge_avg_j3"]))
        elif "forge_level" in secret:
            avg_j1, avg_j3 = war_points.forge_level_to_avg_points(secret["forge_level"])
            secret["forge_avg_j1"] = avg_j1
            secret["forge_avg_j3"] = avg_j3
        return {"screen_type": screen_type, "visible": visible, "secret": secret}

    # 3. TECH TREE — Universal signature: 2..6 percentage cards ('57.4%', '9.55%', '27.6%', '7.66%') in middle band
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
        is_max = _detect_summon_maxed(items, y_min=0.68, y_max=0.80)
        if is_max is not None:
            secret["max_competences"] = is_max
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
        is_max = _detect_summon_maxed(items, y_min=0.50, y_max=0.64)
        if is_max is not None:
            secret["max_oeufs"] = is_max
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
        is_max = _detect_summon_maxed(items, y_min=0.66, y_max=0.78)
        if is_max is not None:
            secret["max_montures"] = is_max
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
        # Secret Forge level (0..35): any box with a trailing integer inside the right blue Forge button
        for it in items:
            if 0.62 <= it["cx"] <= 0.86 and 0.74 <= it["cy"] <= 0.82:
                lm = _LAST_INT_RE.search(it["norm"])
                if lm:
                    lvl = max(0, min(35, int(lm.group(1))))
                    avg_j1, avg_j3 = war_points.forge_level_to_avg_points(lvl)
                    secret["forge_level"] = lvl
                    secret["max_forge"] = 1 if lvl >= 35 else 0
                    secret["forge_avg_j1"] = avg_j1
                    secret["forge_avg_j3"] = avg_j3
                    secret["forge_tier_j1"] = int(round(avg_j1))
                    secret["forge_tier_j3"] = int(round(avg_j3))
                    break
        return {"screen_type": screen_type, "visible": visible, "secret": secret}

    return {"screen_type": screen_type, "visible": visible, "secret": secret}


def scan_image_bytes(data: bytes) -> Dict[str, Any]:
    """Run local RapidOCR with upscaling + high-contrast fallback on raw image bytes."""
    raw_im = Image.open(io.BytesIO(data)).convert("RGB")
    im = _preprocess_image(raw_im)
    res = parse_ocr_items(_ocr_to_items(im), image=raw_im)

    if res["screen_type"] == "unknown" or (not res["visible"] and not res["secret"]):
        boosted = ImageOps.autocontrast(im, cutoff=1)
        boosted = ImageEnhance.Contrast(boosted).enhance(1.35)
        res2 = parse_ocr_items(_ocr_to_items(boosted), image=raw_im)
        if res2["screen_type"] != "unknown" and (len(res2["visible"]) + len(res2["secret"])) >= (len(res["visible"]) + len(res["secret"])):
            return res2
    return res


_INLINE_SCORE_RE = re.compile(
    r"^\s*([A-Za-z0-9_\u00C0-\u017F][A-Za-z0-9_\s.\-\u00C0-\u017F]{1,28}?)\s*[-:|]\s*([0-9][0-9\s.,]*[kmbt]?)\s*(?:pts?|points?)?\s*$",
    re.IGNORECASE,
)


def parse_war_leaderboard_items(items: List[Dict[str, Any]]) -> List[Tuple[str, int]]:
    """
    Extract (player_name, score_points) pairs from a Forge Master war leaderboard screenshot.
    Supports:
    1. Stacked Forge Master modal cards (player name at y, numeric score directly below at y + 0.01..0.045 inside x=0.33..0.76).
    2. Inline 'Name - 1.5m points' lines.
    """
    results: List[Tuple[str, int]] = []
    used_ids = set()

    # 1. Inline 'Name - 1.5m points' lines
    for idx, it in enumerate(items):
        m = _INLINE_SCORE_RE.match(it["text"].strip())
        if m:
            name = m.group(1).strip()
            raw_num = m.group(2).replace(",", "") if re.match(r"^\d{1,3}(?:,\d{3})+$", m.group(2).strip()) else m.group(2)
            amt = _extract_amount(raw_num)
            if amt is not None and amt > 0 and not name.isdigit():
                results.append((name, amt))
                used_ids.add(idx)

    # 2. Stacked Forge Master war modal cards (strictly inside white modal: x=0.33..0.76, y=0.31..0.74)
    modal_items = [
        (idx, it) for idx, it in enumerate(items)
        if idx not in used_ids and 0.33 <= it["cx"] <= 0.76 and 0.31 <= it["cy"] <= 0.74
    ]
    modal_items.sort(key=lambda pair: (pair[1]["cy"], pair[1]["cx"]))

    def _parse_score(txt: str) -> Optional[int]:
        s = _fix_digit_typos(txt.strip())
        if re.match(r"^\d{1,3}(?:,\d{3})+$", s):
            s = s.replace(",", "")
        elif re.match(r"^\d{1,3}(?:\.\d{3})+$", s):
            s = s.replace(".", "")
        if not re.fullmatch(r"[0-9]+(?:[.,][0-9]+)?\s*[kmbt]?", s, re.IGNORECASE):
            return None
        return _extract_amount(s)

    for i, (idx_n, it_n) in enumerate(modal_items):
        if idx_n in used_ids:
            continue
        txt_n = it_n["text"].strip()
        # Must be a player name (contains letters, not a pure number, not 'MVP')
        if _parse_score(txt_n) is not None or it_n["norm"] in ("mvp", "quotidien", "hebdomadaire", "daily", "weekly"):
            continue
        if not re.search(r"[A-Za-z\u00C0-\u017F]{2,}", txt_n):
            continue
        # Look for the numeric score directly below this name (dy in 0.006..0.048, |dx| <= 0.22)
        for idx_s, it_s in modal_items[i + 1 :]:
            if idx_s in used_ids:
                continue
            dy = it_s["cy"] - it_n["cy"]
            if dy > 0.048:
                break
            if dy >= 0.006 and abs(it_s["cx"] - it_n["cx"]) <= 0.22:
                score = _parse_score(it_s["text"])
                if score is not None and score > 0:
                    cleaned_name = re.sub(r"^[#\d.\s)\-]+", "", txt_n).strip()
                    if len(cleaned_name) >= 2:
                        results.append((cleaned_name, score))
                        used_ids.add(idx_n)
                        used_ids.add(idx_s)
                    break

    return results


def _detect_leaderboard_period(
    im: Optional[Image.Image],
    items: List[Dict[str, Any]],
) -> Tuple[str, Optional[int]]:
    """
    Detect whether a Forge Master war leaderboard screenshot is 'daily' (Quotidien) or 'weekly' (Hebdomadaire),
    plus the detected war day (1..5) if daily.
    100% language-independent via the active bright-blue tab at the bottom of the modal (left=daily, right=weekly),
    with multilingual header text fallback ('Jour 2'/'Day 2'/'Dia 2' vs 'Hebdomadaire'/'Weekly'/'Semanal').
    """
    detected_day: Optional[int] = None
    # Check subtitle at y ~ 0.28..0.33 for a day number (e.g. 'Jour 2 - Classement...')
    for it in items:
        if 0.27 <= it["cy"] <= 0.33 and 0.25 <= it["cx"] <= 0.75:
            m = re.search(r"\b([1-5])\b", it["norm"])
            if m:
                detected_day = int(m.group(1))
            if any(w in it["norm"] for w in ("hebdomad", "weekly", "semanal", "wochent")):
                return "weekly", None

    # Language-independent color check on bottom tab bar (~y=0.73..0.77, left x=0.34 vs right x=0.65)
    if im is not None:
        w, h = im.size
        left_blue = 0
        right_blue = 0
        for ry in (0.735, 0.745, 0.755):
            y = min(h - 1, int(h * ry))
            lr, lg, lb = im.getpixel((int(w * 0.34), y))
            rr, rg, rb = im.getpixel((int(w * 0.65), y))
            if lb > lr + 70 and lb > 160:
                left_blue += 1
            if rb > rr + 70 and rb > 160:
                right_blue += 1
        if right_blue > left_blue:
            return "weekly", None
        if left_blue > right_blue:
            return "daily", detected_day

    return "daily", detected_day


def scan_war_leaderboard_full(data: bytes) -> Dict[str, Any]:
    """Return {'period': 'daily'|'weekly', 'day': Optional[int], 'pairs': [(name, pts), ...]}."""
    raw_im = Image.open(io.BytesIO(data)).convert("RGB")
    im = _preprocess_image(raw_im)
    items = _ocr_to_items(im)
    period, day = _detect_leaderboard_period(raw_im, items)
    pairs = parse_war_leaderboard_items(items)
    return {"period": period, "day": day, "pairs": pairs}


def scan_war_leaderboard_bytes(data: bytes) -> List[Tuple[str, int]]:
    return scan_war_leaderboard_full(data)["pairs"]


def match_player_name(ocr_name: str, profiles: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Fuzzy-match an OCR player name against clan profiles."""
    target = _norm(ocr_name)
    if not target:
        return None
    best_p = None
    best_score = 0.0
    for p in profiles:
        pseudo = _norm(p.get("pseudo") or "")
        if not pseudo:
            continue
        if pseudo == target:
            return p
        score = difflib.SequenceMatcher(None, target, pseudo).ratio()
        if score > best_score:
            best_score = score
            best_p = p
    return best_p if best_score >= 0.72 else None
