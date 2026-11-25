import json
import os
from datetime import datetime
from typing import Dict, Any, Optional, Union
import discord

import db
import war_points

RARITY_DISPLAY = [
    ("commun",     "⚪", "Commun"),
    ("rare",       "🔵", "Rare"),
    ("epique",     "🟢", "Épique"),
    ("legendaire", "🟡", "Légendaire"),
    ("ultime",     "🔴", "Ultime"),
    ("mythique",   "🟣", "Mythique"),
    ("heroique",   "🟠", "Héroïque"),
    ("infini",     "🩵", "Infini"),
    ("eternel",    "⭐", "Éternel"),
]


def _format_rarity_lines(rarity_json: str, total: int) -> str:
    try:
        data = json.loads(rarity_json or "{}")
    except (TypeError, ValueError):
        data = {}
    if isinstance(data, dict) and data:
        lines = []
        for key, dot, label in RARITY_DISPLAY:
            cnt = int(data.get(key) or 0)
            if cnt > 0:
                lines.append(f"• {dot} **{label} :** `{db.format_amount(cnt)}`")
        if lines:
            return "\n".join(lines)
    return f"• **Total :** `{db.format_amount(total)}`"

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
GAME_ICON_URL = (
    "https://play-lh.googleusercontent.com/"
    "WGcdbzoUWweXOg3knrsZFyDCqODfblSjIsyDdsEnYot1DBdq4_p38QYN5z-vgS6LyphN5C8HPfSKQyEa6LGAUAk"
)

EMOJI_SPECS = {
    "fm_hammer":      ("fm_hammer.png",      "🔨"),
    "fm_ticket":      ("fm_ticket.png",      "🎟️"),
    "fm_mountkey":    ("fm_mountkey.png",    "🗝️"),
    "fm_potion":      ("fm_potion.png",      "🧪"),
    "fm_greenvial":   ("fm_greenvial.png",   "🧫"),
    "fm_egg":         ("fm_egg.png",         "🥚"),
    "fm_mount_merge": ("fm_mount_merge.png", "🐎"),
    "fm_pet_merge":   ("fm_pet_merge.png",   "🐾"),
}


async def sync_application_emojis(bot: discord.Client) -> Dict[str, discord.Emoji]:
    """Fetch or upload Forge Master sprites as Discord Application Emojis."""
    emoji_map: Dict[str, discord.Emoji] = {}
    try:
        existing = {e.name: e for e in await bot.fetch_application_emojis()}
    except Exception:
        return emoji_map

    for name, (filename, _fallback) in EMOJI_SPECS.items():
        if name in existing:
            emoji_map[name] = existing[name]
            continue
        path = os.path.join(ASSETS_DIR, filename)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as f:
                created = await bot.create_application_emoji(name=name, image=f.read())
                emoji_map[name] = created
        except Exception:
            pass
    return emoji_map


def emoji_str(emoji_map: Dict[str, discord.Emoji], key: str) -> str:
    em = emoji_map.get(key)
    if em is not None:
        return f"<:{em.name}:{em.id}>"
    return EMOJI_SPECS[key][1]


def button_emoji(emoji_map: Dict[str, discord.Emoji], key: str) -> Union[discord.PartialEmoji, str]:
    em = emoji_map.get(key)
    if em is not None:
        return discord.PartialEmoji(name=em.name, id=em.id)
    return EMOJI_SPECS[key][1]


def build_profile_embed(
    profile: Dict[str, Any],
    display_name: str,
    emoji_map: Dict[str, discord.Emoji],
    can_edit: bool = True,
    now: Optional[datetime] = None,
) -> discord.Embed:
    pseudo = profile.get("pseudo") or display_name
    puissance = (profile.get("puissance") or "").strip()
    title = f"{pseudo} — {puissance}" if puissance else pseudo
    e = lambda k: emoji_str(emoji_map, k)
    fmt = lambda k: db.format_amount(int(profile.get(k) or 0))

    embed = discord.Embed(
        title=title,
        color=discord.Color.from_rgb(230, 126, 34),
        description=(
            "\u200b\n"
            f"• {e('fm_hammer')} **Marteaux :** `{fmt('marteaux')}`\n"
            f"• {e('fm_ticket')} **Compétences :** `{fmt('competences')}`\n"
            f"• {e('fm_mountkey')} **Clés Monture :** `{fmt('cles_monture')}`\n"
            f"• {e('fm_potion')} **Potions :** `{fmt('potions')}`\n"
            f"• {e('fm_greenvial')} **Fioles vertes :** `{fmt('fioles_vertes')}`\n"
            f"• {e('fm_egg')} **Œufs :** `{fmt('oeufs')}`"
        ),
    )
    embed.set_thumbnail(url=GAME_ICON_URL)

    fusion_m = int(profile.get("fusion_montures") or 0)
    fusion_p = int(profile.get("fusion_pets") or 0)

    embed.add_field(
        name=f"{e('fm_mount_merge')} Fusion Montures (`{db.format_amount(fusion_m)}`)",
        value=_format_rarity_lines(profile.get("mount_rarity_json") or "{}", fusion_m),
        inline=True,
    )
    embed.add_field(
        name=f"{e('fm_pet_merge')} Fusion Pets (`{db.format_amount(fusion_p)}`)",
        value=_format_rarity_lines(profile.get("egg_rarity_json") or "{}", fusion_p),
        inline=True,
    )

    potentials = war_points.get_today_and_tomorrow_potentials(profile, now=now)
    pot_lines = []
    for item in potentials:
        wday = item["war_day"]
        day_name = item["day_name"]
        prefix = item["prefix"]
        if wday is None:
            pot_lines.append(f"• **{prefix} ({day_name}) :** *Hors guerre*")
        else:
            total_str = db.format_amount(item["total"])
            if item["breakdown"]:
                parts = " • ".join(f"{e(k)} `{db.format_amount(v)}`" for k, v in item["breakdown"])
                pot_lines.append(f"• **{prefix} (J{wday} — {day_name}) :** `~{total_str} pts`\n  └ {parts}")
            else:
                pot_lines.append(f"• **{prefix} (J{wday} — {day_name}) :** `0 pts`")

    embed.add_field(
        name="Points potentiels",
        value="\n".join(pot_lines),
        inline=False,
    )

    footer_text = (
        "Cliquez sur un bouton ci-dessous pour paramétrer vos ressources ou fusions."
        if can_edit
        else f"Profil Forge Master de {pseudo}"
    )
    embed.set_footer(text=footer_text)
    return embed


def build_war_embed(
    war_day: int,
    profiles: list,
    sniper_ids: set,
    emoji_map: Dict[str, discord.Emoji],
    clan_name: str = "Baguette6",
    now: Optional[datetime] = None,
    deductions: Optional[Dict[int, int]] = None,
    resource_filter: str = "all",
    ascension_only: bool = False,
) -> discord.Embed:
    e = lambda k: emoji_str(emoji_map, k)
    current = now.astimezone(war_points.PARIS_TZ) if now is not None else datetime.now(war_points.PARIS_TZ)
    ded_map = deductions or {}

    valid_res = war_points.DAY_VALID_RESOURCES.get(war_day)
    if valid_res is not None and resource_filter != "all" and resource_filter not in valid_res:
        resource_filter = "all"

    resources_line = "  ".join(
        f"{e(icon_key)} {label}" for icon_key, label in war_points.DAY_RESOURCE_LABELS.get(war_day, [])
    )

    active_profiles = [p for p in profiles if int(p.get("include_in_calc", 1)) != 0]
    excluded_count = len(profiles) - len(active_profiles)

    scored = []
    total_ded = 0
    for p in active_profiles:
        uid = int(p["user_id"])
        raw_pts, breakdown = war_points.compute_day_potential(
            p,
            war_day,
            resource_filter=resource_filter,
            ascension_only=ascension_only,
            deducted_pts=0,
        )
        ded = ded_map.get(uid, 0)
        eff_ded = min(raw_pts, ded) if raw_pts > 0 else 0
        pts = raw_pts - eff_ded
        total_ded += eff_ded
        pseudo = p.get("pseudo") or f"Joueur {uid}"
        if pts > 0 or eff_ded > 0 or uid in sniper_ids:
            scored.append((pts, eff_ded, uid, pseudo, breakdown))
    scored.sort(key=lambda x: (-x[0], -x[1], x[3].lower()))

    total_clan = sum(pts for pts, _, _, _, _ in scored)
    snipers = [(pts, uid, pseudo) for pts, _ded, uid, pseudo, _bd in scored if uid in sniper_ids]
    total_snipers = sum(pts for pts, _, _ in snipers)

    mode_str = "✨ Ascension (Max)" if ascension_only else "⚔️ Normal"
    filter_str = war_points.RESOURCE_FILTER_LABELS.get(resource_filter, resource_filter)
    header_meta = f"**Mode :** `{mode_str}`"
    if resource_filter != "all":
        header_meta += f"  •  **Filtre :** `{filter_str}`"
    header_meta += f"  •  **Actifs :** `{len(active_profiles)}`"
    if excluded_count:
        header_meta += f" (`{excluded_count}` exclus)"

    lines = [resources_line, header_meta, ""]
    lines.append("▶️ **Points :**")
    if scored:
        for idx, (pts, eff_ded, uid, pseudo, breakdown) in enumerate(scored[:25], start=1):
            s_icon = "🎯 " if uid in sniper_ids else ""
            ded_txt = f" *[-{db.format_amount(eff_ded)} scanné]*" if eff_ded > 0 else ""
            bd_txt = ""
            if breakdown:
                bd_parts = [f"{e(ic)} {db.format_amount(v)}" for ic, v in breakdown]
                bd_txt = f"  ({', '.join(bd_parts)})"
            lines.append(f"`{idx:>2}.` {s_icon}**{pseudo}** — `{db.format_amount(pts)}` pts{ded_txt}{bd_txt}")
    else:
        lines.append("• *Aucun joueur avec des points dans ce mode.*")

    lines.append("")
    lines.append(f"**Total Points du Clan :** `{db.format_amount(total_clan)}` pts")
    if total_ded > 0:
        lines.append(f"**Déjà scanné (Déduits) :** `-{db.format_amount(total_ded)}` pts")
    lines.append("")
    lines.append(f"🎯 **Snipers : {len(snipers)}**")
    if snipers:
        for pts, _uid, pseudo in snipers:
            lines.append(f"• **{pseudo}** — `{db.format_amount(pts)}` pts")
    else:
        lines.append("• *Aucun sniper inscrit (`/snipe`)*")

    lines.append("")
    lines.append(f"**Total Points Snipers :** `{db.format_amount(total_snipers)}` pts")

    weekly_rows = [
        (int(p.get("weekly_war_pts") or 0), p.get("pseudo") or f"Joueur {p['user_id']}")
        for p in active_profiles
        if int(p.get("weekly_war_pts") or 0) > 0
    ]
    if weekly_rows:
        weekly_rows.sort(key=lambda x: (-x[0], x[1].lower()))
        lines.append("")
        lines.append(f"🗓️ **Hebdo scanné (Top {min(5, len(weekly_rows))}) :**")
        for w_pts, w_pseudo in weekly_rows[:5]:
            lines.append(f"• **{w_pseudo}** — `{db.format_amount(w_pts)}` pts")

    day_label = war_points.WAR_DAY_NAMES.get(war_day, "")
    title = f"⚔️ Points Potentiels Jour {war_day} ({day_label}) — {clan_name}"
    embed = discord.Embed(
        title=title,
        description="\n".join(lines)[:4096],
        color=discord.Color.from_rgb(155, 89, 182) if ascension_only else discord.Color.from_rgb(52, 152, 219),
    )
    embed.set_footer(text=f"{current.strftime('%d-%m-%y %H:%M')} • /snipe pour t'inscrire • /scan pour mettre à jour")
    return embed


class AmountModal(discord.ui.Modal):
    def __init__(
        self,
        view: "ProfileView",
        field_key: str,
        label: str,
        current_value: int,
    ):
        super().__init__(title=f"Modifier — {label}")
        self.profile_view = view
        self.field_key = field_key
        self.amount_input = discord.ui.TextInput(
            label=f"Nombre ({label})",
            placeholder="Ex: 181032, 14.4k, 75.1k, 697",
            default=str(current_value),
            required=True,
            max_length=20,
        )
        self.add_item(self.amount_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            parsed = db.parse_amount(self.amount_input.value)
        except ValueError as err:
            return await interaction.response.send_message(f"❌ {err}", ephemeral=True)

        profile = db.update_profile(
            self.profile_view.user_id,
            {self.field_key: parsed, "pseudo": interaction.user.display_name},
            db_path=self.profile_view.db_path,
            clear_deductions=True,
        )
        embed = build_profile_embed(profile, interaction.user.display_name, self.profile_view.emoji_map, can_edit=True)
        await interaction.response.edit_message(embed=embed, view=self.profile_view)


class ProfileView(discord.ui.View):
    def __init__(
        self,
        user_id: int,
        emoji_map: Dict[str, discord.Emoji],
        db_path: Optional[str] = None,
        timeout: float = 600,
    ):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.emoji_map = emoji_map
        self.db_path = db_path

        buttons = [
            # Row 0: Ressources
            ("marteaux",        "Marteaux",        "fm_hammer",      discord.ButtonStyle.secondary, 0),
            ("competences",     "Compétences",     "fm_ticket",      discord.ButtonStyle.secondary, 0),
            ("cles_monture",    "Clés Monture",    "fm_mountkey",    discord.ButtonStyle.secondary, 0),
            ("potions",         "Potions",         "fm_potion",      discord.ButtonStyle.secondary, 0),
            # Row 1: Fioles vertes, Œufs & Fusions
            ("fioles_vertes",   "Fioles vertes",   "fm_greenvial",   discord.ButtonStyle.secondary, 1),
            ("oeufs",           "Œufs",            "fm_egg",         discord.ButtonStyle.secondary, 1),
            ("fusion_montures", "Fusion Montures", "fm_mount_merge", discord.ButtonStyle.primary,   1),
            ("fusion_pets",     "Fusion Pets",     "fm_pet_merge",   discord.ButtonStyle.primary,   1),
        ]

        for field_key, label, icon_key, style, row in buttons:
            btn = discord.ui.Button(
                label=label,
                emoji=button_emoji(emoji_map, icon_key),
                style=style,
                row=row,
                custom_id=f"fm_cfg_{field_key}",
            )
            btn.callback = self._make_callback(field_key, label)
            self.add_item(btn)

        war_btn = discord.ui.Button(
            label="War",
            emoji="⚔️",
            style=discord.ButtonStyle.success,
            row=2,
            custom_id="fm_open_war",
        )
        war_btn.callback = self._open_war
        self.add_item(war_btn)

    async def _open_war(self, interaction: discord.Interaction):
        today_day, _ = war_points.get_day_info(datetime.now(war_points.PARIS_TZ))
        jour = today_day or 1
        profiles = db.get_all_profiles(db_path=self.db_path)
        sniper_ids = db.get_snipers(jour, db_path=self.db_path)
        deductions = db.get_war_deductions(jour, db_path=self.db_path)
        clan_name = interaction.guild.name if interaction.guild else "Baguette6"
        embed = build_war_embed(
            jour,
            profiles,
            sniper_ids,
            self.emoji_map,
            clan_name=clan_name,
            deductions=deductions,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _check_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ Seul le propriétaire de ce profil peut le modifier. Utilise `/profil` pour le tien.",
                ephemeral=True,
            )
            return False
        return True

    def _make_callback(self, field_key: str, label: str):
        async def callback(interaction: discord.Interaction):
            if not await self._check_owner(interaction):
                return
            profile = db.get_or_create_profile(self.user_id, interaction.user.display_name, db_path=self.db_path)
            modal = AmountModal(self, field_key, label, int(profile.get(field_key) or 0))
            await interaction.response.send_modal(modal)

        return callback
