import json
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from zoneinfo import ZoneInfo

PARIS_TZ = ZoneInfo("Europe/Paris")

# Python weekday(): 0=Lundi, 1=Mardi(J1), 2=Mercredi(J2), 3=Jeudi(J3), 4=Vendredi(J4), 5=Samedi(J5), 6=Dimanche
WEEKDAY_NAMES = {
    0: "Lundi",
    1: "Mardi",
    2: "Mercredi",
    3: "Jeudi",
    4: "Vendredi",
    5: "Samedi",
    6: "Dimanche",
}

WEEKDAY_TO_WAR_DAY: Dict[int, Optional[int]] = {
    0: None,  # Lundi (repos)
    1: 1,     # Mardi = J1
    2: 2,     # Mercredi = J2
    3: 3,     # Jeudi = J3
    4: 4,     # Vendredi = J4
    5: 5,     # Samedi = J5
    6: None,  # Dimanche (repos)
}

WAR_DAY_NAMES: Dict[int, str] = {
    1: "Mardi",
    2: "Mercredi",
    3: "Jeudi",
    4: "Vendredi",
    5: "Samedi",
}

DAY_RESOURCE_LABELS: Dict[int, List[Tuple[str, str]]] = {
    1: [("fm_hammer", "Marteaux"), ("fm_ticket", "Compétences")],
    2: [("fm_mountkey", "Clés Monture"), ("fm_mount_merge", "Fusion Montures")],
    3: [("fm_hammer", "Marteaux"), ("fm_ticket", "Compétences"), ("fm_egg", "Œufs"), ("fm_pet_merge", "Fusion Pets")],
    4: [("fm_mountkey", "Clés Monture"), ("fm_mount_merge", "Fusion Montures")],
    5: [("fm_hammer", "Marteaux"), ("fm_egg", "Œufs"), ("fm_pet_merge", "Fusion Pets")],
}

# Coûts d'invocation moyens (Forge Master)
SKILL_SUMMON_COST = 40      # ~37-50 tickets par invocation selon l'arbre techno
MOUNT_SUMMON_COST = 50      # 48-50 clés par invocation
EGG_SUMMON_COST = 100       # 100 coquilles par œuf invoqué
EGG_MIN_HATCH_PTS = 400     # Minimum garanti (œuf Commun = +400, jusqu'à +65 000 Éternel)

EGG_RARITY_HATCH_PTS: Dict[str, int] = {
    "commun": 400,
    "rare": 1600,
    "epique": 3200,
    "legendaire": 6400,
    "ultime": 12800,
    "mythique": 25600,
    "heroique": 33000,
    "infini": 45000,
    "eternel": 65000,
}


def forge_level_to_avg_points(forge_level: int) -> Tuple[float, float]:
    """Estime (pts moyen J1, pts moyen J3/J5) par marteau selon le niveau de Forge (0..35)."""
    lvl = max(0, min(35, int(forge_level)))
    if lvl <= 6:
        return 2.0, 1.0
    if lvl <= 14:
        ratio = (lvl - 6) / 8.0
        return round(2.0 + 2.0 * ratio, 2), round(1.0 + 2.0 * ratio, 2)
    if lvl <= 24:
        ratio = (lvl - 14) / 10.0
        return round(4.0 + 1.0 * ratio, 2), round(3.0 + 1.0 * ratio, 2)
    return 5.0, 4.0


def expected_egg_hatch_points(egg_rarity_json: str) -> float:
    """Calcule l'espérance de points d'éclosion d'œuf à partir des raretés scannées en backend."""
    try:
        counts = json.loads(egg_rarity_json or "{}")
    except (TypeError, ValueError):
        return float(EGG_MIN_HATCH_PTS)
    if not isinstance(counts, dict) or not counts:
        return float(EGG_MIN_HATCH_PTS)
    total_count = 0
    weighted_sum = 0
    for rarity, cnt in counts.items():
        pts = EGG_RARITY_HATCH_PTS.get(str(rarity).lower())
        if pts and isinstance(cnt, (int, float)) and cnt > 0:
            total_count += int(cnt)
            weighted_sum += int(cnt) * pts
    if total_count <= 0:
        return float(EGG_MIN_HATCH_PTS)
    return round(weighted_sum / total_count, 2)


def get_day_info(dt: datetime) -> Tuple[Optional[int], str]:
    """Retourne (numéro de jour de guerre 1..5 ou None, nom du jour en FR)."""
    wd = dt.weekday()
    return WEEKDAY_TO_WAR_DAY[wd], WEEKDAY_NAMES[wd]


RESOURCE_FILTER_LABELS: Dict[str, str] = {
    "all": "Tout",
    "marteaux": "Marteaux (Forge)",
    "competences": "Compétences",
    "cles_monture": "Clés Monture",
    "oeufs": "Œufs",
    "fusion_montures": "Fusion Montures",
    "fusion_pets": "Fusion Pets",
}


def compute_day_potential(
    profile: Dict[str, Any],
    war_day: Optional[int],
    resource_filter: str = "all",
    ascension_only: bool = False,
    deducted_pts: int = 0,
    respect_include_flag: bool = True,
) -> Tuple[int, List[Tuple[str, int]]]:
    """
    Calcule les points potentiels de guerre de clan pour un jour donné (J1..J5).
    - Respecte include_in_calc (si 0 -> 0 pts).
    - Respecte les tags Max/Ascension (max_forge, max_competences, max_montures, max_oeufs) :
      * Normal (ascension_only=False) : ignore les ressources taguées Max.
      * Ascension (ascension_only=True) : ne compte QUE les ressources taguées Max.
    - Respecte resource_filter ('all' ou une ressource précise).
    - Déduit deducted_pts (scan quotidien admin).
    """
    if war_day is None:
        return 0, []
    if respect_include_flag and int(profile.get("include_in_calc", 1)) == 0:
        return 0, []

    marteaux = int(profile.get("marteaux") or 0)
    competences = int(profile.get("competences") or 0)
    cles_monture = int(profile.get("cles_monture") or 0)
    oeufs = int(profile.get("oeufs") or 0)
    fusion_montures = int(profile.get("fusion_montures") or 0)
    fusion_pets = int(profile.get("fusion_pets") or 0)

    max_flags = {
        "marteaux": bool(int(profile.get("max_forge") or 0)),
        "competences": bool(int(profile.get("max_competences") or 0)),
        "cles_monture": bool(int(profile.get("max_montures") or 0)),
        "oeufs": bool(int(profile.get("max_oeufs") or 0)),
        "fusion_montures": False,
        "fusion_pets": False,
    }

    def allow(res_key: str) -> bool:
        if resource_filter != "all" and resource_filter != res_key:
            return False
        is_max = max_flags.get(res_key, False)
        if ascension_only:
            return is_max
        return not is_max

    # Paramètres secrets stockés par joueur (repli sur les estimations par défaut)
    skill_cost = max(1.0, float(profile.get("skill_summon_cost") or SKILL_SUMMON_COST))
    mount_cost = max(1.0, float(profile.get("mount_summon_cost") or MOUNT_SUMMON_COST))
    egg_cost = max(1.0, float(profile.get("egg_summon_cost") or EGG_SUMMON_COST))
    forge_avg_j1 = float(profile.get("forge_avg_j1") or profile.get("forge_tier_j1") or 5.0)
    forge_avg_j3 = float(profile.get("forge_avg_j3") or profile.get("forge_tier_j3") or 4.0)
    avg_egg_pts = expected_egg_hatch_points(profile.get("egg_rarity_json") or "{}")

    breakdown: List[Tuple[str, int]] = []

    if war_day == 1:
        if allow("marteaux"):
            pts_forge = int(round(marteaux * forge_avg_j1))
            if pts_forge:
                breakdown.append(("fm_hammer", pts_forge))
        if allow("competences"):
            pts_skills = int(competences // skill_cost) * 225
            if pts_skills:
                breakdown.append(("fm_ticket", pts_skills))

    elif war_day == 2:
        if allow("cles_monture"):
            pts_mount_summon = int(cles_monture // mount_cost) * 600
            if pts_mount_summon:
                breakdown.append(("fm_mountkey", pts_mount_summon))
        if allow("fusion_montures"):
            pts_mount_merge = fusion_montures * 600
            if pts_mount_merge:
                breakdown.append(("fm_mount_merge", pts_mount_merge))

    elif war_day == 3:
        if allow("marteaux"):
            pts_forge = int(round(marteaux * forge_avg_j3))
            if pts_forge:
                breakdown.append(("fm_hammer", pts_forge))
        if allow("competences"):
            pts_skills = int(competences // skill_cost) * 175
            if pts_skills:
                breakdown.append(("fm_ticket", pts_skills))
        if allow("oeufs"):
            pts_eggs = int(round((oeufs // egg_cost) * avg_egg_pts))
            if pts_eggs:
                breakdown.append(("fm_egg", pts_eggs))
        if allow("fusion_pets"):
            pts_pet_merge = fusion_pets * 1250
            if pts_pet_merge:
                breakdown.append(("fm_pet_merge", pts_pet_merge))

    elif war_day == 4:
        if allow("cles_monture"):
            pts_mount_summon = int(cles_monture // mount_cost) * 600
            if pts_mount_summon:
                breakdown.append(("fm_mountkey", pts_mount_summon))
        if allow("fusion_montures"):
            pts_mount_merge = fusion_montures * 600
            if pts_mount_merge:
                breakdown.append(("fm_mount_merge", pts_mount_merge))

    elif war_day == 5:
        if allow("marteaux"):
            pts_forge = int(round(marteaux * forge_avg_j3))
            if pts_forge:
                breakdown.append(("fm_hammer", pts_forge))
        if allow("oeufs"):
            pts_eggs = int(round((oeufs // egg_cost) * avg_egg_pts))
            if pts_eggs:
                breakdown.append(("fm_egg", pts_eggs))
        if allow("fusion_pets"):
            pts_pet_merge = fusion_pets * 1250
            if pts_pet_merge:
                breakdown.append(("fm_pet_merge", pts_pet_merge))

    raw_total = sum(pts for _, pts in breakdown)
    total = max(0, raw_total - max(0, int(deducted_pts or 0)))
    return total, breakdown


def get_today_and_tomorrow_potentials(
    profile: Dict[str, Any],
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Retourne les projections pour aujourd'hui et demain (heure de Paris)."""
    current = now.astimezone(PARIS_TZ) if now is not None else datetime.now(PARIS_TZ)
    tomorrow = current + timedelta(days=1)

    results = []
    for label, dt in (("Aujourd'hui", current), ("Demain", tomorrow)):
        war_day, day_name = get_day_info(dt)
        total, breakdown = compute_day_potential(profile, war_day)
        results.append(
            {
                "prefix": label,
                "war_day": war_day,
                "day_name": day_name,
                "total": total,
                "breakdown": breakdown,
            }
        )
    return results
