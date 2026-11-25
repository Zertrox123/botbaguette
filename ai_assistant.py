import asyncio
import glob
import json
import os
from typing import Optional
import aiohttp

import db
import war_points

AI_CHANNEL_ID = 1574928005959127173
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
AI_MODEL = os.getenv("AI_MODEL", "qwen2.5:1.5b")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

_CODE_KEYWORDS = (
    "code", "bug", "fichier", "fonction", "script", "python", "ocr", "scan",
    "calcul", "formule", "sql", "db", "command", "erreur", "impl", "modifier",
)


def build_database_context(db_path: Optional[str] = None) -> str:
    """Build a compact snapshot of the entire SQLite database for the 2B model."""
    profiles = db.get_all_profiles(db_path=db_path)
    lines = ["=== DB PROFILS ==="]
    for p in profiles:
        uid = int(p["user_id"])
        sdays = db.get_user_snipe_days(uid, db_path=db_path)
        pts = [war_points.compute_day_potential(p, d)[0] for d in range(1, 6)]
        lines.append(
            f"id={p.get('short_id')} {p.get('pseudo')}({p.get('puissance') or '?'}) "
            f"calc={p.get('include_in_calc')} F={p.get('forge_level')} "
            f"M={p.get('marteaux')} C={p.get('competences')} CM={p.get('cles_monture')} "
            f"P={p.get('potions')} FV={p.get('fioles_vertes')} O={p.get('oeufs')} "
            f"FM={p.get('fusion_montures')} FP={p.get('fusion_pets')} "
            f"max=({p.get('max_forge')},{p.get('max_competences')},{p.get('max_montures')},{p.get('max_oeufs')}) "
            f"snipe={sdays} J1-5={pts}"
        )
    ded_lines = []
    for d in range(1, 6):
        deds = db.get_war_deductions(d, db_path=db_path)
        if deds:
            ded_lines.append(f"J{d}:{json.dumps(deds)}")
    if ded_lines:
        lines.append("DEDUCTIONS: " + " | ".join(ded_lines))
    return "\n".join(lines)


def build_code_context(prompt: str = "", max_chars: int = 1800) -> str:
    """Include a compact file list always, and code excerpts when the question is about code/logic."""
    files = [os.path.basename(p) for p in sorted(glob.glob(os.path.join(BASE_DIR, "*.py"))) if os.path.basename(p) != "credential.py"]
    low = prompt.lower()
    if not any(kw in low for kw in _CODE_KEYWORDS):
        return "Fichiers bot: " + ", ".join(files)

    chunks = ["=== EXTRAITS CODE ==="]
    total = 0
    for name in files:
        if name.replace(".py", "") in low or total < max_chars:
            try:
                with open(os.path.join(BASE_DIR, name), "r", encoding="utf-8") as f:
                    src = f.read()
            except OSError:
                continue
            snippet = src[:450]
            entry = f"\n[{name}]\n{snippet}"
            if total + len(entry) > max_chars:
                break
            chunks.append(entry)
            total += len(entry)
    return "\n".join(chunks)


async def ask_local_ai(prompt: str, db_path: Optional[str] = None) -> str:
    """Query the local 2B Ollama model with fast, compact DB + codebase context."""
    db_ctx = build_database_context(db_path=db_path)
    code_ctx = build_code_context(prompt)

    system_prompt = (
        "Assistant IA du bot ForgeMaster War (clan Baguette6). "
        "Réponds en français, de façon directe et chiffrée. "
        "Légende DB: M=marteaux, C=compétences, CM=clés monture, P=potions, FV=fioles vertes, O=œufs, FM/FP=fusions, J1-5=points potentiels.\n"
        f"{db_ctx}\n{code_ctx}"
    )

    payload = {
        "model": AI_MODEL,
        "stream": False,
        "keep_alive": "30m",
        "options": {"num_predict": 350, "temperature": 0.4},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
    }

    timeout = aiohttp.ClientTimeout(total=180)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(OLLAMA_URL, json=payload) as resp:
                if resp.status != 200:
                    txt = await resp.text()
                    return f"⚠️ Erreur `{AI_MODEL}` (HTTP {resp.status}) : `{txt[:200]}`"
                data = await resp.json()
                return (data.get("message", {}).get("content") or "").strip() or "*(Réponse vide du modèle)*"
    except asyncio.TimeoutError:
        return f"⏳ Le modèle `{AI_MODEL}` a mis trop de temps à répondre (CPU occupé). Réessaie avec une question plus courte."
    except Exception as exc:
        return (
            f"⚠️ Modèle local `{AI_MODEL}` injoignable sur `{OLLAMA_URL}`.\n"
            f"Détail : `{type(exc).__name__}`"
        )
