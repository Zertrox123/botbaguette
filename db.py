import json
import os
import re
import sqlite3
import time
from typing import Dict, Any, List, Optional, Tuple

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "botbaguette.db")

DEFAULT_PROFILE: Dict[str, Any] = {
    "short_id": 0,
    "pseudo": "",
    "puissance": "",
    "marteaux": 0,
    "competences": 0,
    "cles_monture": 0,
    "potions": 0,
    "fioles_vertes": 0,
    "oeufs": 0,
    "fusion_montures": 0,
    "fusion_pets": 0,
    # Champs secrets (backend — non affichés sur /profil)
    "skill_summon_cost": 40.0,
    "mount_summon_cost": 50.0,
    "egg_summon_cost": 100.0,
    "forge_level": 0,
    "forge_tier_j1": 5,
    "forge_tier_j3": 4,
    "forge_avg_j1": 5.0,
    "forge_avg_j3": 4.0,
    "egg_rarity_json": "{}",
    "mount_rarity_json": "{}",
    "tech_tree_json": "{}",
    # Tags Max / Ascension & calculateur
    "max_forge": 0,
    "max_competences": 0,
    "max_montures": 0,
    "max_oeufs": 0,
    "include_in_calc": 1,
    "updated_at": 0,
}

ALLOWED_COLUMNS = set(DEFAULT_PROFILE.keys())

DEFAULT_ADMIN_STATE: Dict[str, Any] = {
    "selected_day": 0,          # 0 = Vue Globale (J1..J5), 1..5 = Jour précis
    "resource_filter": "all",   # all | marteaux | competences | cles_monture | oeufs | fusion_montures | fusion_pets
    "ascension_only": 0,        # 0 = Normal (exclut Max), 1 = Ascension (uniquement Max)
    "day_plans_json": json.dumps({"1": "PUSH", "2": "SNIPE", "3": "ECO", "4": "ECO", "5": "ALL-IN"}),
}

DEDUCTION_LOCK_SECONDS = 600  # 10 minutes anti-faux-positif

_SUFFIXES = {
    "k": 1_000,
    "m": 1_000_000,
    "b": 1_000_000_000,
    "t": 1_000_000_000_000,
}

_AMOUNT_RE = re.compile(r"^\s*([0-9]+(?:[.,][0-9]+)?)\s*([kmbt])?\s*$", re.IGNORECASE)
_POWER_RE = re.compile(r"([0-9]+(?:[.,][0-9]+)?)\s*([a-z]{1,2})?", re.IGNORECASE)


def normalize_power(raw: str) -> str:
    """Clean OCR/user power strings like '￥369b' or '1.25 t' into '369b' / '1.25t'."""
    cleaned = re.sub(r"^[^0-9]+", "", raw.strip()).replace(",", ".")
    m = _POWER_RE.match(cleaned)
    if not m:
        raise ValueError("Format de puissance invalide (ex: `369b`, `1.25t`).")
    num = m.group(1)
    unit = (m.group(2) or "").lower()
    return f"{num}{unit}"


def parse_amount(raw: str) -> int:
    """Parse Forge Master amounts like '181032', '181 032', '14.4k', '75,1k', '2.08m'."""
    cleaned = raw.replace(" ", "").replace("\u202f", "").replace("\u00a0", "")
    match = _AMOUNT_RE.match(cleaned)
    if not match:
        raise ValueError("Format invalide (ex: `181032`, `14.4k`, `2.08m`).")
    value = float(match.group(1).replace(",", "."))
    suffix = (match.group(2) or "").lower()
    result = int(round(value * _SUFFIXES.get(suffix, 1)))
    if result < 0:
        raise ValueError("La valeur doit être positive.")
    return result


def format_amount(value: int) -> str:
    """Format integer cleanly with spaces, or compact m/b for huge values."""
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}b".rstrip("0").rstrip(".")
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}m".rstrip("0").rstrip(".")
    return f"{value:,}".replace(",", " ")


def format_relative_age(ts: int, now: Optional[int] = None) -> str:
    if not ts:
        return "jamais"
    diff = max(0, int((now if now is not None else time.time()) - ts))
    if diff < 60:
        return "à l'instant"
    if diff < 3600:
        return f"il y a {diff // 60}min"
    if diff < 86400:
        return f"il y a {diff // 3600}h"
    return f"il y a {diff // 86400}j"


def _connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def _next_short_id(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(short_id), 0) AS mx FROM profiles").fetchone()
    return int(row["mx"] or 0) + 1


def init_db(db_path: Optional[str] = None) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                user_id INTEGER PRIMARY KEY,
                short_id INTEGER DEFAULT 0,
                pseudo TEXT DEFAULT '',
                puissance TEXT DEFAULT '',
                marteaux INTEGER DEFAULT 0,
                competences INTEGER DEFAULT 0,
                cles_monture INTEGER DEFAULT 0,
                potions INTEGER DEFAULT 0,
                fioles_vertes INTEGER DEFAULT 0,
                oeufs INTEGER DEFAULT 0,
                fusion_montures INTEGER DEFAULT 0,
                fusion_pets INTEGER DEFAULT 0,
                skill_summon_cost REAL DEFAULT 40.0,
                mount_summon_cost REAL DEFAULT 50.0,
                egg_summon_cost REAL DEFAULT 100.0,
                forge_level INTEGER DEFAULT 0,
                forge_tier_j1 INTEGER DEFAULT 5,
                forge_tier_j3 INTEGER DEFAULT 4,
                forge_avg_j1 REAL DEFAULT 5.0,
                forge_avg_j3 REAL DEFAULT 4.0,
                egg_rarity_json TEXT DEFAULT '{}',
                mount_rarity_json TEXT DEFAULT '{}',
                tech_tree_json TEXT DEFAULT '{}',
                max_forge INTEGER DEFAULT 0,
                max_competences INTEGER DEFAULT 0,
                max_montures INTEGER DEFAULT 0,
                max_oeufs INTEGER DEFAULT 0,
                include_in_calc INTEGER DEFAULT 1,
                updated_at INTEGER DEFAULT 0
            )
            """
        )
        migrations = [
            ("short_id", "INTEGER DEFAULT 0"),
            ("puissance", "TEXT DEFAULT ''"),
            ("fioles_vertes", "INTEGER DEFAULT 0"),
            ("fusion_montures", "INTEGER DEFAULT 0"),
            ("fusion_pets", "INTEGER DEFAULT 0"),
            ("skill_summon_cost", "REAL DEFAULT 40.0"),
            ("mount_summon_cost", "REAL DEFAULT 50.0"),
            ("egg_summon_cost", "REAL DEFAULT 100.0"),
            ("forge_level", "INTEGER DEFAULT 0"),
            ("forge_tier_j1", "INTEGER DEFAULT 5"),
            ("forge_tier_j3", "INTEGER DEFAULT 4"),
            ("forge_avg_j1", "REAL DEFAULT 5.0"),
            ("forge_avg_j3", "REAL DEFAULT 4.0"),
            ("egg_rarity_json", "TEXT DEFAULT '{}'"),
            ("mount_rarity_json", "TEXT DEFAULT '{}'"),
            ("tech_tree_json", "TEXT DEFAULT '{}'"),
            ("max_forge", "INTEGER DEFAULT 0"),
            ("max_competences", "INTEGER DEFAULT 0"),
            ("max_montures", "INTEGER DEFAULT 0"),
            ("max_oeufs", "INTEGER DEFAULT 0"),
            ("include_in_calc", "INTEGER DEFAULT 1"),
        ]
        for col, col_type in migrations:
            try:
                conn.execute(f"ALTER TABLE profiles ADD COLUMN {col} {col_type}")
            except sqlite3.OperationalError:
                pass

        # Backfill short_id for any existing profiles without one
        missing = conn.execute(
            "SELECT user_id FROM profiles WHERE COALESCE(short_id, 0) = 0 ORDER BY rowid ASC"
        ).fetchall()
        for r in missing:
            sid = _next_short_id(conn)
            conn.execute("UPDATE profiles SET short_id = ? WHERE user_id = ?", (sid, r["user_id"]))

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS war_snipes (
                user_id INTEGER NOT NULL,
                war_day INTEGER NOT NULL,
                PRIMARY KEY (user_id, war_day)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS war_deductions (
                user_id INTEGER NOT NULL,
                war_day INTEGER NOT NULL,
                deducted_pts INTEGER DEFAULT 0,
                scanned_at INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, war_day)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_panel_state (
                guild_id INTEGER PRIMARY KEY,
                selected_day INTEGER DEFAULT 0,
                resource_filter TEXT DEFAULT 'all',
                ascension_only INTEGER DEFAULT 0,
                day_plans_json TEXT DEFAULT '{"1":"PUSH","2":"SNIPE","3":"ECO","4":"ECO","5":"ALL-IN"}'
            )
            """
        )
        conn.commit()


def get_or_create_profile(user_id: int, default_pseudo: str = "", db_path: Optional[str] = None) -> Dict[str, Any]:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            now = int(time.time())
            sid = _next_short_id(conn)
            conn.execute(
                "INSERT INTO profiles (user_id, short_id, pseudo, updated_at) VALUES (?, ?, ?, ?)",
                (user_id, sid, default_pseudo, now),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
        profile = dict(row)
        if not profile.get("pseudo") and default_pseudo:
            profile["pseudo"] = default_pseudo
        return profile


def get_profile_by_identifier(identifier: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Lookup profile by short_id ('1'), Discord ID ('5426...'), mention ('<@123>'), or pseudo."""
    cleaned = identifier.strip().lstrip("<@!").rstrip(">")
    with _connect(db_path) as conn:
        if cleaned.isdigit():
            num = int(cleaned)
            row = conn.execute("SELECT * FROM profiles WHERE short_id = ?", (num,)).fetchone()
            if row:
                return dict(row)
            row = conn.execute("SELECT * FROM profiles WHERE user_id = ?", (num,)).fetchone()
            if row:
                return dict(row)
        row = conn.execute(
            "SELECT * FROM profiles WHERE LOWER(pseudo) = LOWER(?)",
            (identifier.strip(),),
        ).fetchone()
        return dict(row) if row else None


def update_profile(
    user_id: int,
    updates: Dict[str, Any],
    db_path: Optional[str] = None,
    touch_updated_at: bool = True,
    clear_deductions: bool = False,
) -> Dict[str, Any]:
    filtered = {k: v for k, v in updates.items() if k in ALLOWED_COLUMNS}
    if not filtered:
        return get_or_create_profile(user_id, db_path=db_path)

    now = int(time.time())
    if touch_updated_at:
        filtered["updated_at"] = now

    with _connect(db_path) as conn:
        exists = conn.execute("SELECT 1 FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
        if not exists:
            sid = _next_short_id(conn)
            conn.execute(
                "INSERT INTO profiles (user_id, short_id, updated_at) VALUES (?, ?, ?)",
                (user_id, sid, now),
            )
        set_clause = ", ".join(f"{col} = ?" for col in filtered.keys())
        values = list(filtered.values()) + [user_id]
        conn.execute(f"UPDATE profiles SET {set_clause} WHERE user_id = ?", values)
        if clear_deductions:
            conn.execute("DELETE FROM war_deductions WHERE user_id = ?", (user_id,))
        conn.commit()
        row = conn.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row)


def get_all_profiles(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM profiles ORDER BY short_id ASC, user_id ASC").fetchall()
        return [dict(r) for r in rows]


def toggle_snipe(user_id: int, war_day: int, db_path: Optional[str] = None) -> bool:
    """Toggle sniper registration for a war day (1..5). Returns True if now active, False if removed."""
    with _connect(db_path) as conn:
        exists = conn.execute(
            "SELECT 1 FROM war_snipes WHERE user_id = ? AND war_day = ?",
            (user_id, war_day),
        ).fetchone()
        if exists:
            conn.execute(
                "DELETE FROM war_snipes WHERE user_id = ? AND war_day = ?",
                (user_id, war_day),
            )
            conn.commit()
            return False
        conn.execute(
            "INSERT INTO war_snipes (user_id, war_day) VALUES (?, ?)",
            (user_id, war_day),
        )
        conn.commit()
        return True


def set_user_snipe_days(user_id: int, days: List[int], db_path: Optional[str] = None) -> List[int]:
    valid = sorted({d for d in days if 1 <= d <= 5})
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM war_snipes WHERE user_id = ?", (user_id,))
        for d in valid:
            conn.execute("INSERT INTO war_snipes (user_id, war_day) VALUES (?, ?)", (user_id, d))
        conn.commit()
    return valid


def get_user_snipe_days(user_id: int, db_path: Optional[str] = None) -> List[int]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT war_day FROM war_snipes WHERE user_id = ? ORDER BY war_day ASC",
            (user_id,),
        ).fetchall()
        return [int(r["war_day"]) for r in rows]


def get_snipers(war_day: int, db_path: Optional[str] = None) -> set:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT user_id FROM war_snipes WHERE war_day = ?",
            (war_day,),
        ).fetchall()
        return {int(r["user_id"]) for r in rows}


def get_all_snipe_user_ids(db_path: Optional[str] = None) -> set:
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT DISTINCT user_id FROM war_snipes").fetchall()
        return {int(r["user_id"]) for r in rows}


def reset_all_snipes(db_path: Optional[str] = None) -> None:
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM war_snipes")
        conn.commit()


def record_war_deduction(
    user_id: int,
    war_day: int,
    deducted_pts: int,
    now: Optional[int] = None,
    db_path: Optional[str] = None,
) -> Tuple[bool, int]:
    """
    Record daily scanned war points to deduct from a player's potential for `war_day`.
    Enforces a 10-minute lock per player/day to prevent false positives.
    Returns (applied: bool, remaining_lock_seconds: int).
    """
    ts = int(now if now is not None else time.time())
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT scanned_at FROM war_deductions WHERE user_id = ? AND war_day = ?",
            (user_id, war_day),
        ).fetchone()
        if row and (ts - int(row["scanned_at"] or 0)) < DEDUCTION_LOCK_SECONDS:
            rem = DEDUCTION_LOCK_SECONDS - (ts - int(row["scanned_at"] or 0))
            return False, rem
        conn.execute(
            """
            INSERT INTO war_deductions (user_id, war_day, deducted_pts, scanned_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, war_day) DO UPDATE SET
                deducted_pts = excluded.deducted_pts,
                scanned_at = excluded.scanned_at
            """,
            (user_id, war_day, max(0, int(deducted_pts)), ts),
        )
        conn.commit()
        return True, 0


def get_war_deductions(war_day: int, db_path: Optional[str] = None) -> Dict[int, int]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT user_id, deducted_pts FROM war_deductions WHERE war_day = ?",
            (war_day,),
        ).fetchall()
        return {int(r["user_id"]): int(r["deducted_pts"] or 0) for r in rows}


def reset_war_deductions(war_day: Optional[int] = None, db_path: Optional[str] = None) -> None:
    with _connect(db_path) as conn:
        if war_day is None:
            conn.execute("DELETE FROM war_deductions")
        else:
            conn.execute("DELETE FROM war_deductions WHERE war_day = ?", (war_day,))
        conn.commit()


def get_admin_state(guild_id: int = 0, db_path: Optional[str] = None) -> Dict[str, Any]:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM admin_panel_state WHERE guild_id = ?", (guild_id,)).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO admin_panel_state (guild_id, selected_day, resource_filter, ascension_only, day_plans_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    DEFAULT_ADMIN_STATE["selected_day"],
                    DEFAULT_ADMIN_STATE["resource_filter"],
                    DEFAULT_ADMIN_STATE["ascension_only"],
                    DEFAULT_ADMIN_STATE["day_plans_json"],
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM admin_panel_state WHERE guild_id = ?", (guild_id,)).fetchone()
        return dict(row)


def update_admin_state(guild_id: int, updates: Dict[str, Any], db_path: Optional[str] = None) -> Dict[str, Any]:
    allowed = {"selected_day", "resource_filter", "ascension_only", "day_plans_json"}
    filtered = {k: v for k, v in updates.items() if k in allowed}
    state = get_admin_state(guild_id, db_path=db_path)
    if not filtered:
        return state
    set_clause = ", ".join(f"{col} = ?" for col in filtered.keys())
    values = list(filtered.values()) + [guild_id]
    with _connect(db_path) as conn:
        conn.execute(f"UPDATE admin_panel_state SET {set_clause} WHERE guild_id = ?", values)
        conn.commit()
        row = conn.execute("SELECT * FROM admin_panel_state WHERE guild_id = ?", (guild_id,)).fetchone()
        return dict(row)
