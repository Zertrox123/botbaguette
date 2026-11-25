import glob
import json
import os
from typing import Optional
import aiohttp

import db
import war_points

AI_CHANNEL_ID = 1574928005959127173
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
AI_MODEL = os.getenv("AI_MODEL", "gemma2:2b")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def build_database_context(db_path: Optional[str] = None) -> str:
    """Build a compact snapshot of the entire SQLite database for the 2B model."""
    profiles = db.get_all_profiles(db_path=db_path)
    lines = ["=== DATABASE: PROFILES ==="]
    for p in profiles:
        uid = int(p["user_id"])
        sdays = db.get_user_snipe_days(uid, db_path=db_path)
        pts = [war_points.compute_day_potential(p, d)[0] for d in range(1, 6)]
        lines.append(
            f"id={p.get('short_id')} pseudo={p.get('pseudo')} pwr={p.get('puissance')} "
            f"calc={p.get('include_in_calc')} forge={p.get('forge_level')} "
            f"marteaux={p.get('marteaux')} comp={p.get('competences')} cles_m={p.get('cles_monture')} "
            f"potions={p.get('potions')} fioles_v={p.get('fioles_vertes')} oeufs={p.get('oeufs')} "
            f"fus_m={p.get('fusion_montures')} fus_p={p.get('fusion_pets')} "
            f"max=(F:{p.get('max_forge')},C:{p.get('max_competences')},M:{p.get('max_montures')},O:{p.get('max_oeufs')}) "
            f"snipe={sdays} pts_J1_J5={pts}"
        )
    lines.append("\n=== DEDUCTIONS DU JOUR ===")
    for d in range(1, 6):
        deds = db.get_war_deductions(d, db_path=db_path)
        if deds:
            lines.append(f"J{d}: {json.dumps(deds)}")
    return "\n".join(lines)


def build_code_context(max_chars: int = 8000) -> str:
    """Build a compact index of the bot's Python source files for the 2B model."""
    chunks = ["=== CODEBASE ==="]
    total = 0
    for path in sorted(glob.glob(os.path.join(BASE_DIR, "*.py"))):
        name = os.path.basename(path)
        if name == "credential.py":
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                src = f.read()
        except OSError:
            continue
        snippet = src[:1400]
        entry = f"\n--- {name} ({len(src)} chars) ---\n{snippet}"
        if total + len(entry) > max_chars:
            break
        chunks.append(entry)
        total += len(entry)
    return "\n".join(chunks)


async def ask_local_ai(prompt: str, db_path: Optional[str] = None) -> str:
    """Query the local 2B Ollama model with full DB + codebase context."""
    db_ctx = build_database_context(db_path=db_path)
    code_ctx = build_code_context()

    system_prompt = (
        "Tu es l'assistant IA stratégique du bot Discord ForgeMaster War (clan Baguette6). "
        "Réponds en français, de façon concise, précise et chiffrée. "
        "Tu as accès en direct à toute la base de données du clan et au code du bot ci-dessous.\n\n"
        f"{db_ctx}\n\n{code_ctx}"
    )

    payload = {
        "model": AI_MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
    }

    timeout = aiohttp.ClientTimeout(total=90)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(OLLAMA_URL, json=payload) as resp:
                if resp.status != 200:
                    txt = await resp.text()
                    return f"⚠️ Erreur du modèle local `{AI_MODEL}` (HTTP {resp.status}) : `{txt[:200]}`"
                data = await resp.json()
                return (data.get("message", {}).get("content") or "").strip() or "*(Réponse vide du modèle)*"
    except Exception as exc:
        return (
            f"⚠️ Modèle local `{AI_MODEL}` injoignable sur `{OLLAMA_URL}`.\n"
            f"Assure-toi qu'Ollama tourne sur le serveur (`ollama pull {AI_MODEL}`).\n"
            f"Détail : `{type(exc).__name__}`"
        )
