import asyncio
from collections import deque
from datetime import datetime
import glob
import json
import os
import re
from typing import Any, Deque, Dict, Optional
import aiohttp

import db
import war_points

AI_CHANNEL_ID = 1574928005959127173
AI_URL = os.getenv("AI_URL", "http://127.0.0.1:1234/api/v1/chat")
AI_MODEL = os.getenv("AI_MODEL", "qwen/qwen3-vl-8b")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Mémoire courte des 6 derniers échanges (12 messages) du salon IA
_CHAT_HISTORY: Deque[Dict[str, str]] = deque(maxlen=12)
_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


def build_database_context(db_path: Optional[str] = None) -> str:
    """Build a complete snapshot of the entire SQLite database with precomputed summaries."""
    profiles = db.get_all_profiles(db_path=db_path)
    lines = ["=== RESUME CLAN (PRE-CALCULE) ==="]
    dated = [p for p in profiles if int(p.get("updated_at") or 0) > 0]
    if dated:
        oldest = min(dated, key=lambda p: int(p["updated_at"]))
        newest = max(dated, key=lambda p: int(p["updated_at"]))
        o_dt = datetime.fromtimestamp(int(oldest["updated_at"]), tz=war_points.PARIS_TZ).strftime("%d/%m/%Y %H:%M")
        n_dt = datetime.fromtimestamp(int(newest["updated_at"]), tz=war_points.PARIS_TZ).strftime("%d/%m/%Y %H:%M")
        lines.append(f"Plus ancienne MAJ: {oldest.get('pseudo')} ({o_dt}, {db.format_relative_age(int(oldest['updated_at']))})")
        lines.append(f"Plus recente MAJ: {newest.get('pseudo')} ({n_dt}, {db.format_relative_age(int(newest['updated_at']))})")
    totals = [
        sum(war_points.compute_day_potential(p, d)[0] for p in profiles)
        for d in range(1, 6)
    ]
    lines.append(f"Total points potentiels clan J1..J5: J1={totals[0]}, J2={totals[1]}, J3={totals[2]}, J4={totals[3]}, J5={totals[4]}")

    lines.append("\n=== DB PROFILS (DONNEES LIVE) ===")
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
            f"id={p.get('short_id')} discord_id={uid} pseudo={p.get('pseudo')} puissance={p.get('puissance') or '?'} "
            f"maj={upd_str} calc={p.get('include_in_calc')} Forge={p.get('forge_level')} "
            f"Marteaux={p.get('marteaux')} Comp={p.get('competences')} ClesM={p.get('cles_monture')} "
            f"Potions={p.get('potions')} FiolesV={p.get('fioles_vertes')} Oeufs={p.get('oeufs')} "
            f"FusM={p.get('fusion_montures')} FusP={p.get('fusion_pets')} "
            f"Max(F/C/M/O)=({p.get('max_forge')},{p.get('max_competences')},{p.get('max_montures')},{p.get('max_oeufs')}) "
            f"Snipe={sdays} Hebdo={p.get('weekly_war_pts') or 0} J1={pts[0]} J2={pts[1]} J3={pts[2]} J4={pts[3]} J5={pts[4]}"
        )
    ded_lines = []
    for d in range(1, 6):
        deds = db.get_war_deductions(d, db_path=db_path)
        if deds:
            ded_lines.append(f"J{d}:{json.dumps(deds)}")
    if ded_lines:
        lines.append("DEDUCTIONS: " + " | ".join(ded_lines))
    return "\n".join(lines)


def build_code_context(max_chars: Optional[int] = None) -> str:
    """Include the bot Python source code (excluding credential.py) as context."""
    files = [
        os.path.basename(p)
        for p in sorted(glob.glob(os.path.join(BASE_DIR, "*.py")))
        if os.path.basename(p) != "credential.py"
    ]
    chunks = ["=== CODE SOURCE DU BOT ==="]
    total = 0
    for name in files:
        try:
            with open(os.path.join(BASE_DIR, name), "r", encoding="utf-8") as f:
                src = f.read()
        except OSError:
            continue
        entry = f"\n--- {name} ---\n{src}"
        if max_chars is not None and total + len(entry) > max_chars:
            rem = max_chars - total
            if rem > 200:
                chunks.append(entry[:rem] + "\n...[tronqué]")
            break
        chunks.append(entry)
        total += len(entry)
    return "\n".join(chunks)


def _extract_reply(data: Any) -> str:
    """Extract assistant reply text from LM Studio /api/v1/chat (or OpenAI/Ollama fallback)."""
    if not isinstance(data, dict):
        return ""
    output = data.get("output")
    if isinstance(output, list):
        msg_parts = [
            str(item.get("content") or "")
            for item in output
            if isinstance(item, dict) and item.get("type") == "message"
        ]
        if not msg_parts:
            msg_parts = [
                str(item.get("content") or "")
                for item in output
                if isinstance(item, dict) and item.get("type") != "reasoning" and item.get("content")
            ]
        text = "\n".join(p for p in msg_parts if p).strip()
        if text:
            return _THINK_RE.sub("", text).strip()
    if isinstance(data.get("message"), dict) and data["message"].get("content"):
        return _THINK_RE.sub("", str(data["message"]["content"])).strip()
    choices = data.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        msg = choices[0].get("message") or {}
        if isinstance(msg, dict) and msg.get("content"):
            return _THINK_RE.sub("", str(msg["content"])).strip()
    for key in ("response", "content", "text"):
        if data.get(key):
            return _THINK_RE.sub("", str(data[key])).strip()
    return ""


def _build_system_prompt(author_name: str, db_ctx: str, code_ctx: str) -> str:
    return (
        "Tu es l'assistant IA du bot Discord ForgeMaster War pour le jeu mobile Forge Master (clan Baguette6). "
        f"La personne qui te parle actuellement s'appelle {author_name}.\n"
        "Règles strictes :\n"
        "- Tu AS accès en direct à toute la base de données dans DB PROFILS et à tout le code source du bot ci-dessous.\n"
        "- Réponds en français, de façon naturelle, directe et précise (ne répète jamais le pseudo entre crochets).\n"
        "- N'attribue JAMAIS les stats d'un joueur à un autre joueur.\n"
        "- Si un joueur n'apparaît pas dans DB PROFILS, dis clairement qu'il n'a pas encore enregistré son profil (/profil ou /scan).\n"
        "- N'invente jamais de chiffres absents de la DB.\n\n"
        f"{db_ctx}\n\n{code_ctx}"
    )


def _format_input_with_history(author_name: str, prompt: str) -> str:
    if not _CHAT_HISTORY:
        return f"({author_name}) {prompt}"
    hist = []
    for msg in _CHAT_HISTORY:
        role = "Utilisateur" if msg["role"] == "user" else "Assistant"
        hist.append(f"{role}: {msg['content']}")
    hist.append(f"Utilisateur: ({author_name}) {prompt}")
    return "\n".join(hist)


async def ask_local_ai(
    prompt: str,
    author_name: str = "Utilisateur",
    db_path: Optional[str] = None,
) -> str:
    """Query the user-hosted LM Studio /api/v1/chat endpoint with full DB and code context."""
    db_ctx = build_database_context(db_path=db_path)
    full_code_ctx = build_code_context(max_chars=None)
    user_input = _format_input_with_history(author_name, prompt)

    timeout = aiohttp.ClientTimeout(total=180)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Try full DB + full code first; if server rejects context size (HTTP 400), retry with trimmed code.
            for code_ctx in (full_code_ctx, build_code_context(max_chars=8000)):
                payload = {
                    "model": AI_MODEL,
                    "system_prompt": _build_system_prompt(author_name, db_ctx, code_ctx),
                    "input": user_input,
                }
                async with session.post(AI_URL, json=payload) as resp:
                    if resp.status == 400 and code_ctx is full_code_ctx:
                        continue
                    if resp.status != 200:
                        txt = await resp.text()
                        return f"⚠️ Erreur `{AI_MODEL}` (HTTP {resp.status}) : `{txt[:200]}`"
                    data = await resp.json()
                    reply = _extract_reply(data) or "*(Réponse vide du modèle)*"
                    _CHAT_HISTORY.append({"role": "user", "content": f"({author_name}) {prompt}"})
                    _CHAT_HISTORY.append({"role": "assistant", "content": reply})
                    return reply
    except asyncio.TimeoutError:
        return f"⏳ Le modèle `{AI_MODEL}` a mis trop de temps à répondre. Réessaie."
    except Exception as exc:
        return (
            f"⚠️ Modèle IA `{AI_MODEL}` injoignable sur `{AI_URL}`.\n"
            f"Détail : `{type(exc).__name__}`"
        )
    return "*(Réponse vide du modèle)*"
