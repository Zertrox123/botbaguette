import asyncio
from collections import deque
from datetime import datetime
import glob
import json
import os
from typing import Deque, Dict, Optional
import aiohttp

import db
import war_points

AI_CHANNEL_ID = 1574928005959127173
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
AI_MODEL = os.getenv("AI_MODEL", "qwen2.5:3b")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Mémoire courte des 6 derniers échanges (12 messages) du salon IA
_CHAT_HISTORY: Deque[Dict[str, str]] = deque(maxlen=12)

_CODE_KEYWORDS = (
    "code", "bug", "fichier", "fonction", "script", "python", "ocr", "scan",
    "calcul", "formule", "sql", "db", "command", "erreur", "impl", "modifier",
)


def build_database_context(db_path: Optional[str] = None) -> str:
    """Build a complete, compact snapshot of the entire SQLite database for the local model."""
    profiles = db.get_all_profiles(db_path=db_path)
    lines = ["=== DB PROFILS (DONNEES LIVE) ==="]
    for p in profiles:
        uid = int(p["user_id"])
        sdays = db.get_user_snipe_days(uid, db_path=db_path)
        pts = [war_points.compute_day_potential(p, d)[0] for d in range(1, 6)]
        upd_ts = int(p.get("updated_at") or 0)
        if upd_ts > 0:
            upd_dt = datetime.fromtimestamp(upd_ts, tz=war_points.PARIS_TZ).strftime("%d/%m/%Y %H:%M")
            upd_str = f"{upd_dt} ({db.format_relative_age(upd_ts)})"
        else:
            upd_str = "jamais"
        lines.append(
            f"id={p.get('short_id')} pseudo={p.get('pseudo')} puissance={p.get('puissance') or '?'} "
            f"maj={upd_str} calc={p.get('include_in_calc')} Forge={p.get('forge_level')} "
            f"Marteaux={p.get('marteaux')} Comp={p.get('competences')} ClesM={p.get('cles_monture')} "
            f"Potions={p.get('potions')} FiolesV={p.get('fioles_vertes')} Oeufs={p.get('oeufs')} "
            f"FusM={p.get('fusion_montures')} FusP={p.get('fusion_pets')} "
            f"Max(F/C/M/O)=({p.get('max_forge')},{p.get('max_competences')},{p.get('max_montures')},{p.get('max_oeufs')}) "
            f"Snipe={sdays} Pts_J1_a_J5={pts}"
        )
    ded_lines = []
    for d in range(1, 6):
        deds = db.get_war_deductions(d, db_path=db_path)
        if deds:
            ded_lines.append(f"J{d}:{json.dumps(deds)}")
    if ded_lines:
        lines.append("DEDUCTIONS: " + " | ".join(ded_lines))
    return "\n".join(lines)


def build_code_context(prompt: str = "", max_chars: int = 1600) -> str:
    """Include a compact file list always, and code excerpts when the question is about code/logic."""
    files = [
        os.path.basename(p)
        for p in sorted(glob.glob(os.path.join(BASE_DIR, "*.py")))
        if os.path.basename(p) != "credential.py"
    ]
    low = prompt.lower()
    if not any(kw in low for kw in _CODE_KEYWORDS):
        return "Fichiers du bot : " + ", ".join(files)

    chunks = ["=== EXTRAITS CODE ==="]
    total = 0
    for name in files:
        if name.replace(".py", "") in low or total < max_chars:
            try:
                with open(os.path.join(BASE_DIR, name), "r", encoding="utf-8") as f:
                    src = f.read()
            except OSError:
                continue
            snippet = src[:400]
            entry = f"\n[{name}]\n{snippet}"
            if total + len(entry) > max_chars:
                break
            chunks.append(entry)
            total += len(entry)
    return "\n".join(chunks)


async def ask_local_ai(
    prompt: str,
    author_name: str = "Utilisateur",
    db_path: Optional[str] = None,
) -> str:
    """Query the local Ollama model with author identity, short chat memory, and live DB context."""
    db_ctx = build_database_context(db_path=db_path)
    code_ctx = build_code_context(prompt)

    system_prompt = (
        "Tu es l'assistant IA du bot Discord ForgeMaster War pour le jeu mobile Forge Master (clan Baguette6). "
        f"La personne qui te parle actuellement s'appelle {author_name}.\n"
        "Règles strictes :\n"
        "- Tu AS accès en direct à toute la base de données dans DB PROFILS ci-dessous (incluant la date de mise à jour `maj=...`). Utilise-la toujours pour répondre.\n"
        "- Réponds en français, de façon naturelle, directe et précise (ne répète jamais le pseudo entre crochets).\n"
        "- N'attribue JAMAIS les stats d'un joueur à un autre joueur.\n"
        "- Si un joueur n'apparaît pas dans DB PROFILS, dis clairement qu'il n'a pas encore enregistré son profil (/profil ou /scan).\n"
        "- N'invente jamais de chiffres absents de la DB.\n\n"
        f"{db_ctx}\n{code_ctx}"
    )

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(list(_CHAT_HISTORY))
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": AI_MODEL,
        "stream": False,
        "keep_alive": "30m",
        "options": {"num_predict": 350, "temperature": 0.25},
        "messages": messages,
    }

    timeout = aiohttp.ClientTimeout(total=240)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(OLLAMA_URL, json=payload) as resp:
                if resp.status != 200:
                    txt = await resp.text()
                    return f"⚠️ Erreur `{AI_MODEL}` (HTTP {resp.status}) : `{txt[:200]}`"
                data = await resp.json()
                reply = (data.get("message", {}).get("content") or "").strip() or "*(Réponse vide du modèle)*"
                _CHAT_HISTORY.append({"role": "user", "content": f"({author_name}) {prompt}"})
                _CHAT_HISTORY.append({"role": "assistant", "content": reply})
                return reply
    except asyncio.TimeoutError:
        return f"⏳ Le modèle `{AI_MODEL}` a mis trop de temps à répondre (CPU occupé). Réessaie avec une question plus courte."
    except Exception as exc:
        return (
            f"⚠️ Modèle local `{AI_MODEL}` injoignable sur `{OLLAMA_URL}`.\n"
            f"Détail : `{type(exc).__name__}`"
        )
