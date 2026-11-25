import asyncio
import time
from datetime import datetime
from typing import Dict, Any, List, Optional
import discord

import db
import profile_ui
import scanner
import war_points

ADMIN_USER_IDS = {542655055803842572, 164319847021412352}
MAX_SANE_EGGS = 10_000_000

ASC_SPECS = [
    ("forge", "max_forge", "marteaux", "fm_hammer", "Forge"),
    ("competences", "max_competences", "competences", "fm_ticket", "Compétences"),
    ("montures", "max_montures", "cles_monture", "fm_mountkey", "Montures"),
    ("oeufs", "max_oeufs", "oeufs", "fm_egg", "Œufs"),
]


def build_ascensions_embed(
    clan_name: str,
    emoji_map: Dict[str, discord.Emoji],
    res_filter: str = "all",
    db_path: Optional[str] = None,
) -> discord.Embed:
    e = lambda k: profile_ui.emoji_str(emoji_map, k)
    profiles = [p for p in db.get_all_profiles(db_path=db_path) if int(p.get("include_in_calc", 1)) != 0]
    now_str = datetime.now(war_points.PARIS_TZ).strftime("%d-%m-%y %H:%M")

    if res_filter != "all":
        spec = next((s for s in ASC_SPECS if s[0] == res_filter), ASC_SPECS[0])
        _key, max_col, amt_col, icon, label = spec
        max_list = []
        non_max_big = []
        for p in profiles:
            amt = int(p.get(amt_col) or 0)
            if amt_col == "oeufs" and amt > MAX_SANE_EGGS:
                continue
            pseudo = p.get("pseudo") or f"Joueur {p['user_id']}"
            if int(p.get(max_col) or 0) == 1:
                max_list.append((amt, pseudo))
            elif amt >= 50_000:
                non_max_big.append((amt, pseudo))
        max_list.sort(key=lambda x: (-x[0], x[1].lower()))
        non_max_big.sort(key=lambda x: (-x[0], x[1].lower()))
        total_stock = sum(amt for amt, _ in max_list)

        lines = [f"`{idx:>2}.` **{pseudo}** — `{db.format_amount(amt)}`" for idx, (amt, pseudo) in enumerate(max_list, start=1)]
        if not lines:
            lines.append("*Aucun joueur Max sur cette ressource.*")

        embed = discord.Embed(
            title=f"{e(icon)} Ascensions — {label} ({clan_name})",
            color=discord.Color.from_rgb(155, 89, 182),
            description=(
                f"**Joueurs Max :** `{len(max_list)}`  •  "
                f"**Stock cumulé (Max) :** `{db.format_amount(total_stock)}`"
            ),
        )
        embed.add_field(
            name=f"🏆 Classement Joueurs Max ({len(max_list)})",
            value="\n".join(lines)[:1024],
            inline=False,
        )
        if non_max_big:
            embed.add_field(
                name="📦 Gros stocks non-Max (≥ 50k)",
                value="\n".join(f"• **{ps}** — `{db.format_amount(am)}`" for am, ps in non_max_big[:6])[:1024],
                inline=False,
            )
        embed.set_footer(text=f"Panel Admin • {now_str} • /membre [id] pour éditer")
        return embed

    # 2x2 overview grid across all 4 Ascension resources
    total_max = 0
    unique_users = set()
    cards = []
    for _key, max_col, amt_col, icon, label in ASC_SPECS:
        max_rows = []
        for p in profiles:
            if int(p.get(max_col) or 0) == 1:
                amt = int(p.get(amt_col) or 0)
                if amt_col == "oeufs" and amt > MAX_SANE_EGGS:
                    amt = 0
                unique_users.add(int(p["user_id"]))
                max_rows.append((amt, p.get("pseudo") or f"Joueur {p['user_id']}"))
        max_rows.sort(key=lambda x: (-x[0], x[1].lower()))
        total_max += len(max_rows)

        if max_rows:
            top_lines = [f"`{idx}.` **{ps}** — `{db.format_amount(am)}`" for idx, (am, ps) in enumerate(max_rows[:4], start=1)]
            if len(max_rows) > 4:
                top_lines.append(f"*+{len(max_rows) - 4} autre(s) (filtre ci-dessous)*")
            body = "\n".join(top_lines)
        else:
            body = "*Aucun joueur Max*"
        cards.append((f"{e(icon)} {label} ({len(max_rows)})", body))

    embed = discord.Embed(
        title=f"✨ Dashboard Ascensions — {clan_name}",
        color=discord.Color.from_rgb(155, 89, 182),
        description=(
            f"**Total ressources Max :** `{total_max}` ({len(unique_users)} joueur(s))  •  "
            f"**Membres actifs :** `{len(profiles)}`"
        ),
    )
    for title, body in cards:
        embed.add_field(name=title, value=body, inline=True)
    embed.set_footer(text=f"Panel Admin • {now_str} • Utilise le filtre pour voir tout un classement")
    return embed


def build_eggs_fusions_embed(
    clan_name: str,
    emoji_map: Dict[str, discord.Emoji],
    db_path: Optional[str] = None,
) -> discord.Embed:
    e = lambda k: profile_ui.emoji_str(emoji_map, k)
    profiles = [p for p in db.get_all_profiles(db_path=db_path) if int(p.get("include_in_calc", 1)) != 0]
    now_str = datetime.now(war_points.PARIS_TZ).strftime("%d-%m-%y %H:%M")

    can_open = []
    should_hold = []
    merge_only = []
    tot_eggs = 0
    tot_fus = 0
    outliers = []

    for p in profiles:
        eggs = int(p.get("oeufs") or 0)
        fus = int(p.get("fusion_pets") or 0)
        pseudo = p.get("pseudo") or f"Joueur {p['user_id']}"
        is_max = bool(int(p.get("max_oeufs") or 0))
        if eggs > MAX_SANE_EGGS:
            outliers.append(pseudo)
            eggs = 0
        tot_eggs += eggs
        tot_fus += fus
        if eggs >= 20_000 and fus < 300:
            can_open.append((eggs, fus, pseudo, is_max))
        elif eggs >= 20_000 and fus >= 300:
            should_hold.append((eggs, fus, pseudo, is_max))
        elif fus >= 200:
            merge_only.append((fus, eggs, pseudo, is_max))

    can_open.sort(key=lambda x: (-x[0], x[2].lower()))
    should_hold.sort(key=lambda x: (-x[0], -x[1], x[2].lower()))
    merge_only.sort(key=lambda x: (-x[0], -x[1], x[2].lower()))

    def fmt_row(eggs: int, fus: int, pseudo: str, is_max: bool) -> str:
        mtag = " `MAX`" if is_max else ""
        return f"• **{pseudo}**{mtag} — {e('fm_egg')} `{db.format_amount(eggs)}`  •  {e('fm_pet_merge')} `{db.format_amount(fus)}` fusions"

    embed = discord.Embed(
        title=f"{e('fm_egg')} Suivi Œufs & Fusions Pets — {clan_name}",
        color=discord.Color.from_rgb(46, 204, 113),
        description=(
            f"{e('fm_egg')} **Total Coquilles :** `{db.format_amount(tot_eggs)}`  •  "
            f"{e('fm_pet_merge')} **Total Fusions Pets :** `{db.format_amount(tot_fus)}`"
            + (f"\n⚠️ *Scan œufs à corriger : {', '.join(outliers)}*" if outliers else "")
        ),
    )
    embed.add_field(
        name=f"🟢 Peuvent ouvrir (≥ 20k coquilles, < 300 fusions) — {len(can_open)}",
        value="\n".join(fmt_row(eg, fu, ps, mx) for eg, fu, ps, mx in can_open[:8]) or "*Aucun joueur*",
        inline=False,
    )
    embed.add_field(
        name=f"🟡 À garder / Attendre (≥ 20k coquilles + ≥ 300 fusions) — {len(should_hold)}",
        value="\n".join(fmt_row(eg, fu, ps, mx) for eg, fu, ps, mx in should_hold[:8]) or "*Aucun joueur*",
        inline=False,
    )
    embed.add_field(
        name=f"🧬 Fusions prêtes sans gros stock d'œufs (≥ 200 fusions) — {len(merge_only)}",
        value="\n".join(fmt_row(eg, fu, ps, mx) for fu, eg, ps, mx in merge_only[:10]) or "*Aucun joueur*",
        inline=False,
    )
    embed.set_footer(text=f"Panel Admin • {now_str} • /membre [id] pour éditer")
    return embed


async def check_admin_access(interaction: discord.Interaction) -> bool:
    if interaction.user.id not in ADMIN_USER_IDS:
        await interaction.response.send_message(
            "❌ Accès refusé : cette commande est réservée aux administrateurs autorisés.",
            ephemeral=True,
        )
        return False
    return True


def _get_active_day_for_details(selected_day: int, now: Optional[datetime] = None) -> int:
    if 1 <= selected_day <= 5:
        return selected_day
    current = now.astimezone(war_points.PARIS_TZ) if now is not None else datetime.now(war_points.PARIS_TZ)
    today, _ = war_points.get_day_info(current)
    return today or 1


def build_admin_panel_embed(
    guild_id: int,
    clan_name: str,
    emoji_map: Dict[str, discord.Emoji],
    db_path: Optional[str] = None,
    now: Optional[datetime] = None,
) -> discord.Embed:
    state = db.get_admin_state(guild_id, db_path=db_path)
    selected_day = int(state.get("selected_day") or 0)
    res_filter = str(state.get("resource_filter") or "all")
    valid_res = war_points.DAY_VALID_RESOURCES.get(selected_day)
    if valid_res is not None and res_filter != "all" and res_filter not in valid_res:
        res_filter = "all"
        db.update_admin_state(guild_id, {"resource_filter": "all"}, db_path=db_path)
    ascension_only = bool(int(state.get("ascension_only") or 0))

    profiles = db.get_all_profiles(db_path=db_path)
    e = lambda k: profile_ui.emoji_str(emoji_map, k)
    current = now.astimezone(war_points.PARIS_TZ) if now is not None else datetime.now(war_points.PARIS_TZ)
    now_ts = int(current.timestamp())

    included_profiles = [p for p in profiles if int(p.get("include_in_calc", 1)) != 0]
    fresh_count = sum(1 for p in included_profiles if p.get("updated_at") and (now_ts - int(p["updated_at"])) < 86400)
    excluded_count = len(profiles) - len(included_profiles)

    day_str = "Vue Globale (J1 → J5)" if selected_day == 0 else f"Jour {selected_day} ({war_points.WAR_DAY_NAMES.get(selected_day, '')})"
    filter_str = war_points.RESOURCE_FILTER_LABELS.get(res_filter, res_filter)
    mode_str = "✨ ASCENSION (Ressources Max uniquement)" if ascension_only else "⚔️ NORMAL (Ressources Max exclues)"

    # Compact 48-char table so it never wraps inside Discord PC embeds
    table_lines = [
        f"{'Jour':<11} | {'Clan Pts':>8} | {'Snipers':>12} | {'Déduits':>8}",
        "-" * 48,
    ]
    days_to_show = [1, 2, 3, 4, 5] if selected_day == 0 else [selected_day]
    for d in days_to_show:
        snipers = db.get_snipers(d, db_path=db_path)
        deductions = db.get_war_deductions(d, db_path=db_path)
        clan_pts = 0
        snipe_pts = 0
        ded_sum = 0
        for p in included_profiles:
            uid = int(p["user_id"])
            ded = deductions.get(uid, 0)
            raw_pts, _ = war_points.compute_day_potential(
                p, d, resource_filter=res_filter, ascension_only=ascension_only, deducted_pts=0
            )
            eff_ded = min(raw_pts, ded) if raw_pts > 0 else 0
            pts = raw_pts - eff_ded
            ded_sum += eff_ded
            clan_pts += pts
            if uid in snipers:
                snipe_pts += pts
        d_label = f"J{d} {war_points.WAR_DAY_NAMES[d]}"
        snipe_col = f"{db.format_amount(snipe_pts)} ({len(snipers)})"
        ded_col = f"-{db.format_amount(ded_sum)}" if ded_sum > 0 else "0"
        table_lines.append(
            f"{d_label:<11} | {db.format_amount(clan_pts):>8} | {snipe_col:>12} | {ded_col:>8}"
        )

    # Global stock aggregation
    tot_h = sum(int(p.get("marteaux") or 0) for p in included_profiles)
    tot_c = sum(int(p.get("competences") or 0) for p in included_profiles)
    tot_mk = sum(int(p.get("cles_monture") or 0) for p in included_profiles)
    tot_p = sum(int(p.get("potions") or 0) for p in included_profiles)
    tot_gv = sum(int(p.get("fioles_vertes") or 0) for p in included_profiles)
    tot_e = sum(int(p.get("oeufs") or 0) for p in included_profiles)
    tot_fm = sum(int(p.get("fusion_montures") or 0) for p in included_profiles)
    tot_fp = sum(int(p.get("fusion_pets") or 0) for p in included_profiles)
    forge_lvls = [int(p.get("forge_level") or 0) for p in included_profiles if int(p.get("forge_level") or 0) > 0]
    avg_forge = f"{sum(forge_lvls) / len(forge_lvls):.1f}" if forge_lvls else "N/A"

    # Detail Top 10 for the inspected day
    detail_day = _get_active_day_for_details(selected_day, now=current)
    detail_snipers = db.get_snipers(detail_day, db_path=db_path)
    detail_deds = db.get_war_deductions(detail_day, db_path=db_path)
    ranked = []
    for p in included_profiles:
        uid = int(p["user_id"])
        ded = detail_deds.get(uid, 0)
        raw_pts, _ = war_points.compute_day_potential(
            p, detail_day, resource_filter=res_filter, ascension_only=ascension_only, deducted_pts=0
        )
        eff_ded = min(raw_pts, ded) if raw_pts > 0 else 0
        pts = raw_pts - eff_ded
        ranked.append((pts, p, uid in detail_snipers, eff_ded))
    ranked.sort(key=lambda x: (-x[0], (x[1].get("pseudo") or "").lower()))

    top_lines = []
    for idx, (pts, p, is_snipe, ded) in enumerate(ranked[:10], start=1):
        pseudo = p.get("pseudo") or f"Joueur {p['user_id']}"
        sid = p.get("short_id") or 0
        pwr = f" ({p['puissance']})" if p.get("puissance") else ""
        s_icon = "🎯 " if is_snipe else ""
        ded_txt = f"  *[-{db.format_amount(ded)} scanné]*" if ded > 0 else ""
        top_lines.append(
            f"`{idx:>2}.` {s_icon}**{pseudo}**{pwr} `(id:{sid})` — `{db.format_amount(pts)} pts`{ded_txt}"
        )
    if not top_lines:
        top_lines.append("*Aucun membre actif dans le calculateur.*")

    embed = discord.Embed(
        title=f"🛡️ Panel Admin — Stratégie de Guerre ({clan_name})",
        color=discord.Color.from_rgb(155, 89, 182) if ascension_only else discord.Color.from_rgb(43, 45, 49),
        description=(
            f"**Mode :** {mode_str}\n"
            f"**Jour :** `{day_str}`  •  **Filtre :** `{filter_str}`\n"
            f"**Membres :** `{len(included_profiles)}` actifs (`{fresh_count}` < 24h) • `{excluded_count}` exclus\n"
            f"```text\n" + "\n".join(table_lines) + "\n```"
        ),
    )

    embed.add_field(
        name="📦 Stock Global (Actifs)",
        value=(
            f"{e('fm_hammer')} `{db.format_amount(tot_h)}`  •  "
            f"{e('fm_ticket')} `{db.format_amount(tot_c)}`  •  "
            f"{e('fm_mountkey')} `{db.format_amount(tot_mk)}`\n"
            f"{e('fm_potion')} `{db.format_amount(tot_p)}`  •  "
            f"{e('fm_greenvial')} `{db.format_amount(tot_gv)}`  •  "
            f"{e('fm_egg')} `{db.format_amount(tot_e)}`"
        ),
        inline=True,
    )
    embed.add_field(
        name="🧬 Fusions & Forge",
        value=(
            f"{e('fm_mount_merge')} **Montures :** `{db.format_amount(tot_fm)}`\n"
            f"{e('fm_pet_merge')} **Pets :** `{db.format_amount(tot_fp)}`\n"
            f"🔥 **Forge moy. :** `{avg_forge}`"
        ),
        inline=True,
    )
    embed.add_field(
        name=f"🏆 Top 10 — Jour {detail_day} ({war_points.WAR_DAY_NAMES[detail_day]})",
        value="\n".join(top_lines),
        inline=False,
    )
    embed.set_footer(text=f"Sauvegardé automatiquement • {current.strftime('%d-%m-%y %H:%M')} • /membre [id] pour éditer")
    return embed


def build_members_page_embed(
    profiles: List[Dict[str, Any]],
    snipe_day: int,
    sniper_ids: set,
    page: int,
    per_page: int = 10,
    now_ts: Optional[int] = None,
) -> discord.Embed:
    ts = int(now_ts if now_ts is not None else time.time())
    total_pages = max(1, (len(profiles) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    chunk = profiles[page * per_page : (page + 1) * per_page]

    lines = []
    for p in chunk:
        uid = int(p["user_id"])
        sid = int(p.get("short_id") or 0)
        pseudo = p.get("pseudo") or f"Joueur {uid}"
        age = db.format_relative_age(int(p.get("updated_at") or 0), now=ts)
        snipe_mark = "🟢" if uid in sniper_ids else "🔴"
        calc_tag = "" if int(p.get("include_in_calc", 1)) else " `[EXCLU]`"
        max_tags = []
        if int(p.get("max_forge") or 0):
            max_tags.append("F")
        if int(p.get("max_competences") or 0):
            max_tags.append("C")
        if int(p.get("max_montures") or 0):
            max_tags.append("M")
        if int(p.get("max_oeufs") or 0):
            max_tags.append("O")
        max_str = f" `MAX:{'/'.join(max_tags)}`" if max_tags else ""
        lines.append(f"• **{pseudo}** — {age} `(id: {sid})`{max_str}{calc_tag}  {snipe_mark}")

    if not lines:
        lines.append("*Aucun membre enregistré.*")

    embed = discord.Embed(
        title=f"👥 Membres du Clan — Snipe J{snipe_day} ({war_points.WAR_DAY_NAMES.get(snipe_day, '')})",
        description="\u200b\n" + "\n".join(lines),
        color=discord.Color.from_rgb(52, 152, 219),
    )
    embed.set_footer(text=f"Page {page + 1}/{total_pages} • Utilise /membre [id] pour modifier un membre • 🟢/🔴 = Snipe J{snipe_day}")
    return embed


class MembersPaginationView(discord.ui.View):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_admin_access(interaction)

    def __init__(
        self,
        guild_id: int,
        clan_name: str,
        emoji_map: Dict[str, discord.Emoji],
        snipe_day: int,
        db_path: Optional[str] = None,
    ):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.clan_name = clan_name
        self.emoji_map = emoji_map
        self.snipe_day = snipe_day
        self.db_path = db_path
        self.page = 0

    def _current_embed(self) -> discord.Embed:
        profiles = db.get_all_profiles(db_path=self.db_path)
        sniper_ids = db.get_snipers(self.snipe_day, db_path=self.db_path)
        return build_members_page_embed(profiles, self.snipe_day, sniper_ids, self.page)

    @discord.ui.button(label="Précédent", emoji="⬅️", style=discord.ButtonStyle.secondary)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        await interaction.response.edit_message(embed=self._current_embed(), view=self)

    @discord.ui.button(label="Suivant", emoji="➡️", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        profiles = db.get_all_profiles(db_path=self.db_path)
        max_page = max(0, (len(profiles) - 1) // 10)
        self.page = min(max_page, self.page + 1)
        await interaction.response.edit_message(embed=self._current_embed(), view=self)

    @discord.ui.button(label="Retour Panel", emoji="🛡️", style=discord.ButtonStyle.primary)
    async def back_panel(self, interaction: discord.Interaction, button: discord.ui.Button):
        panel_view = AdminPanelView(self.guild_id, self.clan_name, self.emoji_map, active_tab="ascensions", db_path=self.db_path)
        await interaction.response.edit_message(embed=panel_view.current_embed(), view=panel_view)


class FilterView(discord.ui.View):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_admin_access(interaction)

    def __init__(
        self,
        guild_id: int,
        clan_name: str,
        emoji_map: Dict[str, discord.Emoji],
        db_path: Optional[str] = None,
    ):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.clan_name = clan_name
        self.emoji_map = emoji_map
        self.db_path = db_path
        state = db.get_admin_state(guild_id, db_path=db_path)
        self.cur_res = str(state.get("resource_filter") or "all")
        self.cur_day = int(state.get("selected_day") or 0)
        valid = war_points.DAY_VALID_RESOURCES.get(self.cur_day)
        if valid is not None and self.cur_res != "all" and self.cur_res not in valid:
            self.cur_res = "all"

        self.res_select = discord.ui.Select(
            placeholder="Choisir la ressource à calculer",
            options=self._res_options(),
            row=0,
        )
        self.res_select.callback = self._on_res_change
        self.add_item(self.res_select)

        self.day_select = discord.ui.Select(
            placeholder="Choisir le jour de guerre",
            options=self._day_options(),
            row=1,
        )
        self.day_select.callback = self._on_day_change
        self.add_item(self.day_select)

    def _res_options(self) -> List[discord.SelectOption]:
        valid = war_points.DAY_VALID_RESOURCES.get(self.cur_day)
        return [
            discord.SelectOption(label=lbl, value=k, default=(k == self.cur_res))
            for k, lbl in war_points.RESOURCE_FILTER_LABELS.items()
            if k == "all" or valid is None or k in valid
        ]

    def _day_options(self) -> List[discord.SelectOption]:
        return [
            discord.SelectOption(label="Vue Globale (J1 → J5)", value="0", default=(self.cur_day == 0)),
            discord.SelectOption(label="Jour 1 — Mardi", value="1", default=(self.cur_day == 1)),
            discord.SelectOption(label="Jour 2 — Mercredi", value="2", default=(self.cur_day == 2)),
            discord.SelectOption(label="Jour 3 — Jeudi", value="3", default=(self.cur_day == 3)),
            discord.SelectOption(label="Jour 4 — Vendredi", value="4", default=(self.cur_day == 4)),
            discord.SelectOption(label="Jour 5 — Samedi", value="5", default=(self.cur_day == 5)),
        ]

    def _sync_selects(self) -> None:
        self.res_select.options = self._res_options()
        self.day_select.options = self._day_options()

    async def _on_res_change(self, interaction: discord.Interaction):
        self.cur_res = interaction.data["values"][0]
        db.update_admin_state(self.guild_id, {"resource_filter": self.cur_res}, db_path=self.db_path)
        self._sync_selects()
        embed = build_admin_panel_embed(self.guild_id, self.clan_name, self.emoji_map, db_path=self.db_path)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_day_change(self, interaction: discord.Interaction):
        self.cur_day = int(interaction.data["values"][0])
        updates: Dict[str, Any] = {"selected_day": self.cur_day}
        valid = war_points.DAY_VALID_RESOURCES.get(self.cur_day)
        if valid is not None and self.cur_res != "all" and self.cur_res not in valid:
            self.cur_res = "all"
            updates["resource_filter"] = "all"
        db.update_admin_state(self.guild_id, updates, db_path=self.db_path)
        self._sync_selects()
        embed = build_admin_panel_embed(self.guild_id, self.clan_name, self.emoji_map, db_path=self.db_path)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Valider & Retour Panel", emoji="✅", style=discord.ButtonStyle.success, row=2)
    async def done(self, interaction: discord.Interaction, button: discord.ui.Button):
        panel_view = AdminPanelView(self.guild_id, self.clan_name, self.emoji_map, active_tab="war", db_path=self.db_path)
        await interaction.response.edit_message(embed=panel_view.current_embed(), view=panel_view)


class AdminPanelView(discord.ui.View):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_admin_access(interaction)

    def __init__(
        self,
        guild_id: int,
        clan_name: str,
        emoji_map: Dict[str, discord.Emoji],
        active_tab: str = "ascensions",
        asc_filter: str = "all",
        db_path: Optional[str] = None,
    ):
        super().__init__(timeout=600)
        self.guild_id = guild_id
        self.clan_name = clan_name
        self.emoji_map = emoji_map
        self.active_tab = active_tab  # ascensions | eggs | war
        self.asc_filter = asc_filter  # all | forge | competences | montures | oeufs
        self.db_path = db_path
        self._rebuild_items()

    def current_embed(self) -> discord.Embed:
        if self.active_tab == "eggs":
            return build_eggs_fusions_embed(self.clan_name, self.emoji_map, db_path=self.db_path)
        if self.active_tab == "war":
            return build_admin_panel_embed(self.guild_id, self.clan_name, self.emoji_map, db_path=self.db_path)
        return build_ascensions_embed(self.clan_name, self.emoji_map, res_filter=self.asc_filter, db_path=self.db_path)

    def _rebuild_items(self) -> None:
        self.clear_items()

        # Row 0: main navigation tabs + Members
        b_asc = discord.ui.Button(
            label="Ascensions",
            emoji="✨",
            style=discord.ButtonStyle.primary if self.active_tab == "ascensions" else discord.ButtonStyle.secondary,
            row=0,
        )
        b_asc.callback = self._go_ascensions
        self.add_item(b_asc)

        b_eggs = discord.ui.Button(
            label="Œufs & Fusions",
            emoji="🥚",
            style=discord.ButtonStyle.success if self.active_tab == "eggs" else discord.ButtonStyle.secondary,
            row=0,
        )
        b_eggs.callback = self._go_eggs
        self.add_item(b_eggs)

        b_war = discord.ui.Button(
            label="Points & Scan",
            emoji="⚔️",
            style=discord.ButtonStyle.primary if self.active_tab == "war" else discord.ButtonStyle.secondary,
            row=0,
        )
        b_war.callback = self._go_war
        self.add_item(b_war)

        b_mem = discord.ui.Button(
            label="Membres",
            emoji="👥",
            style=discord.ButtonStyle.secondary,
            row=0,
        )
        b_mem.callback = self._members_cb
        self.add_item(b_mem)

        # Row 1: contextual controls for the active tab
        if self.active_tab == "ascensions":
            asc_select = discord.ui.Select(
                placeholder="Filtrer les Ascensions par ressource...",
                row=1,
                options=[
                    discord.SelectOption(label="✨ Toutes les Ascensions (Vue 2×2)", value="all", default=(self.asc_filter == "all")),
                    discord.SelectOption(label="🔨 Forge (Marteaux)", value="forge", default=(self.asc_filter == "forge")),
                    discord.SelectOption(label="🎟️ Compétences", value="competences", default=(self.asc_filter == "competences")),
                    discord.SelectOption(label="🗝️ Montures (Clés)", value="montures", default=(self.asc_filter == "montures")),
                    discord.SelectOption(label="🥚 Œufs (Coquilles)", value="oeufs", default=(self.asc_filter == "oeufs")),
                ],
            )
            asc_select.callback = self._on_asc_filter
            self.add_item(asc_select)
        elif self.active_tab == "eggs":
            b_ref = discord.ui.Button(label="Actualiser", emoji="🔄", style=discord.ButtonStyle.secondary, row=1)
            b_ref.callback = self._refresh_cb
            self.add_item(b_ref)
        elif self.active_tab == "war":
            state = db.get_admin_state(self.guild_id, db_path=self.db_path)
            asc_on = bool(int(state.get("ascension_only") or 0))

            b_scan = discord.ui.Button(label="Scan Guerre", emoji="📸", style=discord.ButtonStyle.primary, row=1)
            b_scan.callback = self._scan_war_cb
            self.add_item(b_scan)

            b_filt = discord.ui.Button(label="Filtre", emoji="🔍", style=discord.ButtonStyle.secondary, row=1)
            b_filt.callback = self._filter_cb
            self.add_item(b_filt)

            b_ascmode = discord.ui.Button(
                label="Ascension : ON" if asc_on else "Ascension",
                emoji="✨",
                style=discord.ButtonStyle.success if asc_on else discord.ButtonStyle.secondary,
                row=1,
            )
            b_ascmode.callback = self._ascension_mode_cb
            self.add_item(b_ascmode)

            b_reset = discord.ui.Button(label="Reset Déductions", emoji="🔄", style=discord.ButtonStyle.danger, row=1)
            b_reset.callback = self._reset_ded_cb
            self.add_item(b_reset)

    async def _go_ascensions(self, interaction: discord.Interaction):
        self.active_tab = "ascensions"
        self._rebuild_items()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    async def _go_eggs(self, interaction: discord.Interaction):
        self.active_tab = "eggs"
        self._rebuild_items()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    async def _go_war(self, interaction: discord.Interaction):
        self.active_tab = "war"
        self._rebuild_items()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    async def _on_asc_filter(self, interaction: discord.Interaction):
        self.asc_filter = interaction.data["values"][0]
        self.active_tab = "ascensions"
        self._rebuild_items()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    async def _refresh_cb(self, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    async def _scan_war_cb(self, interaction: discord.Interaction):
        state = db.get_admin_state(self.guild_id, db_path=self.db_path)
        target_day = _get_active_day_for_details(int(state.get("selected_day") or 0))
        await interaction.response.send_message(
            f"📸 **Scan Guerre — Jour {target_day} ({war_points.WAR_DAY_NAMES[target_day]})**\n"
            "Envoie maintenant tes captures du classement de guerre dans ce salon (**jusqu'à 20 images** en un ou plusieurs messages, 90s max).\n"
            "Tape `ok` quand tu as fini (ou attends la fin du délai).",
        )

        bot = interaction.client
        collected: List[discord.Attachment] = []
        deadline = time.monotonic() + 90.0

        def check(msg: discord.Message) -> bool:
            return msg.author.id == interaction.user.id and msg.channel.id == interaction.channel_id

        while len(collected) < 20:
            rem = deadline - time.monotonic()
            if rem <= 0:
                break
            try:
                msg = await bot.wait_for("message", check=check, timeout=min(rem, 25.0))
            except asyncio.TimeoutError:
                if collected:
                    break
                continue
            if msg.content.strip().lower() in ("ok", "stop", "fin", "done"):
                break
            imgs = [a for a in msg.attachments if not a.content_type or a.content_type.startswith("image/")]
            if imgs:
                collected.extend(imgs[: 20 - len(collected)])
                if len(collected) >= 20:
                    break

        if not collected:
            return await interaction.followup.send("❌ Aucune image reçue pour le scan de guerre.")

        profiles = db.get_all_profiles(db_path=self.db_path)
        applied_daily = []
        applied_weekly = []
        unmatched = []

        for att in collected[:20]:
            raw = await att.read()
            scan_res = await asyncio.to_thread(scanner.scan_war_leaderboard_full, raw)
            period = scan_res["period"]
            eff_day = scan_res["day"] or target_day
            for ocr_name, pts in scan_res["pairs"]:
                matched = scanner.match_player_name(ocr_name, profiles)
                if not matched:
                    unmatched.append(f"{ocr_name} ({db.format_amount(pts)})")
                    continue
                uid = int(matched["user_id"])
                if period == "weekly":
                    db.update_profile(
                        uid,
                        {"weekly_war_pts": pts},
                        db_path=self.db_path,
                        touch_updated_at=False,
                    )
                    applied_weekly.append(f"• **{matched['pseudo']}** : `{db.format_amount(pts)} pts` *(Hebdo — non déduit du daily)*")
                else:
                    db.record_war_deduction(uid, eff_day, pts, db_path=self.db_path)
                    applied_daily.append(f"• **{matched['pseudo']}** : `-{db.format_amount(pts)} pts` (J{eff_day})")

        report = [f"📊 **Résultat du Scan Guerre ({len(collected)} capture(s))**"]
        if applied_daily:
            report.append("\n**📅 Journalier (déduit du calculateur) :**\n" + "\n".join(applied_daily[:20]))
        if applied_weekly:
            report.append("\n**🗓️ Hebdomadaire (suivi semaine uniquement, non déduit du daily) :**\n" + "\n".join(applied_weekly[:20]))
        if unmatched:
            report.append("\n**Non reconnus dans la DB :** " + ", ".join(unmatched[:10]))
        if not applied_daily and not applied_weekly:
            report.append("\n❌ Aucun joueur du clan reconnu sur ces captures.")

        try:
            await interaction.message.edit(embed=self.current_embed(), view=self)
        except Exception:
            pass
        await interaction.followup.send("\n".join(report))

    async def _members_cb(self, interaction: discord.Interaction):
        state = db.get_admin_state(self.guild_id, db_path=self.db_path)
        day = _get_active_day_for_details(int(state.get("selected_day") or 0))
        view = MembersPaginationView(self.guild_id, self.clan_name, self.emoji_map, snipe_day=day, db_path=self.db_path)
        await interaction.response.edit_message(embed=view._current_embed(), view=view)

    async def _filter_cb(self, interaction: discord.Interaction):
        view = FilterView(self.guild_id, self.clan_name, self.emoji_map, db_path=self.db_path)
        embed = build_admin_panel_embed(self.guild_id, self.clan_name, self.emoji_map, db_path=self.db_path)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _ascension_mode_cb(self, interaction: discord.Interaction):
        state = db.get_admin_state(self.guild_id, db_path=self.db_path)
        new_val = 0 if int(state.get("ascension_only") or 0) else 1
        db.update_admin_state(self.guild_id, {"ascension_only": new_val}, db_path=self.db_path)
        self._rebuild_items()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    async def _reset_ded_cb(self, interaction: discord.Interaction):
        state = db.get_admin_state(self.guild_id, db_path=self.db_path)
        sel = int(state.get("selected_day") or 0)
        db.reset_war_deductions(war_day=(sel if 1 <= sel <= 5 else None), db_path=self.db_path)
        await interaction.response.edit_message(embed=self.current_embed(), view=self)


# ==============================================================================
# /membre [id] — Fiche Admin d'un Membre
# ==============================================================================

def build_member_admin_embed(
    profile: Dict[str, Any],
    snipe_days: List[int],
    deductions: Optional[Dict[int, int]] = None,
) -> discord.Embed:
    sid = profile.get("short_id") or 0
    pseudo = profile.get("pseudo") or f"Joueur {profile['user_id']}"
    age = db.format_relative_age(int(profile.get("updated_at") or 0))
    snipe_str = ", ".join(f"J{d} ({war_points.WAR_DAY_NAMES[d]})" for d in snipe_days) if snipe_days else "Aucun"
    inc_str = "✅ Inclus dans le calculateur" if int(profile.get("include_in_calc", 1)) else "❌ Exclu du calculateur"
    yn = lambda k: "✅ Max / Ascension" if int(profile.get(k) or 0) else "❌ Non"
    ded_map = deductions or {}
    ded_str = (
        ", ".join(f"J{d}: -{db.format_amount(pts)}" for d, pts in sorted(ded_map.items()))
        if ded_map
        else "Aucune"
    )

    embed = discord.Embed(
        title=f"⚙️ Gestion Membre — {pseudo} (id: {sid})",
        color=discord.Color.from_rgb(52, 152, 219),
        description=(
            f"• **Dernière MÀJ profil :** {age}\n"
            f"• **Calculateur :** {inc_str}\n"
            f"• **Snipe :** `{snipe_str}`\n"
            f"• **Déductions actives :** `{ded_str}`\n"
            f"• **Forge (backend) :** `Niv. {int(profile.get('forge_level') or 0)}`"
        ),
    )
    embed.add_field(
        name="✨ Ressources Max / Ascension (exclues en Normal, actives en Ascension)",
        value=(
            f"• **Forge :** {yn('max_forge')}\n"
            f"• **Compétences :** {yn('max_competences')}\n"
            f"• **Montures :** {yn('max_montures')}\n"
            f"• **Œufs :** {yn('max_oeufs')}"
        ),
        inline=False,
    )
    embed.set_footer(text="Chaque clic est immédiatement sauvegardé et appliqué au calculateur.")
    return embed


class MemberSnipeModal(discord.ui.Modal):
    def __init__(self, member_view: "MemberEditView", current_days: List[int]):
        super().__init__(title="Modifier Snipe du membre")
        self.member_view = member_view
        default_str = ", ".join(str(d) for d in current_days) if current_days else "non"
        self.days_input = discord.ui.TextInput(
            label="Jours de snipe (1 à 5) ou 'non'",
            placeholder="Ex: 1, 2, 5  (ou 'non' pour retirer)",
            default=default_str,
            required=True,
            max_length=25,
        )
        self.add_item(self.days_input)

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.days_input.value.strip().lower()
        if raw in ("non", "no", "aucun", "0", ""):
            days: List[int] = []
        else:
            days = [int(ch) for ch in raw if ch in "12345"]
        db.set_user_snipe_days(self.member_view.target_user_id, days, db_path=self.member_view.db_path)
        await self.member_view.refresh(interaction)


class MemberDeductionModal(discord.ui.Modal):
    def __init__(self, member_view: "MemberEditView", default_day: int, current_ded: int):
        super().__init__(title="Modifier Déduction Guerre")
        self.member_view = member_view
        self.day_input = discord.ui.TextInput(
            label="Jour de guerre (1 à 5)",
            placeholder="Ex: 1 (Mardi) ... 5 (Samedi)",
            default=str(default_day),
            required=True,
            max_length=2,
        )
        self.pts_input = discord.ui.TextInput(
            label="Points à déduire (0 pour annuler)",
            placeholder="Ex: 201790, 1.5m ou 0",
            default=str(current_ded),
            required=True,
            max_length=20,
        )
        self.add_item(self.day_input)
        self.add_item(self.pts_input)

    async def on_submit(self, interaction: discord.Interaction):
        raw_day = self.day_input.value.strip()
        if not raw_day.isdigit() or not (1 <= int(raw_day) <= 5):
            return await interaction.response.send_message("❌ Le jour doit être entre `1` et `5`.", ephemeral=True)
        try:
            pts = db.parse_amount(self.pts_input.value)
        except ValueError as err:
            return await interaction.response.send_message(f"❌ {err}", ephemeral=True)
        db.set_war_deduction_manual(
            self.member_view.target_user_id,
            int(raw_day),
            pts,
            db_path=self.member_view.db_path,
        )
        await self.member_view.refresh(interaction)


class MemberEditView(discord.ui.View):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_admin_access(interaction)

    def __init__(self, target_user_id: int, db_path: Optional[str] = None):
        super().__init__(timeout=300)
        self.target_user_id = target_user_id
        self.db_path = db_path
        self._sync_button_styles()

    def _sync_button_styles(self) -> None:
        p = db.get_or_create_profile(self.target_user_id, db_path=self.db_path)
        inc = bool(int(p.get("include_in_calc", 1)))
        self.calc_toggle.style = discord.ButtonStyle.success if inc else discord.ButtonStyle.danger
        self.calc_toggle.label = "Calculateur : ON" if inc else "Calculateur : OFF"

        for btn, key, name in (
            (self.max_forge_btn, "max_forge", "Forge"),
            (self.max_comp_btn, "max_competences", "Compétences"),
            (self.max_mount_btn, "max_montures", "Montures"),
            (self.max_egg_btn, "max_oeufs", "Œufs"),
        ):
            on = bool(int(p.get(key) or 0))
            btn.style = discord.ButtonStyle.success if on else discord.ButtonStyle.secondary
            btn.label = f"{name} : Max" if on else f"{name} : Non"

    async def refresh(self, interaction: discord.Interaction) -> None:
        self._sync_button_styles()
        p = db.get_or_create_profile(self.target_user_id, db_path=self.db_path)
        sdays = db.get_user_snipe_days(self.target_user_id, db_path=self.db_path)
        deds = db.get_user_war_deductions(self.target_user_id, db_path=self.db_path)
        await interaction.response.edit_message(embed=build_member_admin_embed(p, sdays, deductions=deds), view=self)

    @discord.ui.button(label="Modifier Snipe", emoji="🎯", style=discord.ButtonStyle.primary, row=0)
    async def snipe_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        sdays = db.get_user_snipe_days(self.target_user_id, db_path=self.db_path)
        await interaction.response.send_modal(MemberSnipeModal(self, sdays))

    @discord.ui.button(label="Modifier Déduction", emoji="➖", style=discord.ButtonStyle.primary, row=0)
    async def deduction_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        today, _ = war_points.get_day_info(datetime.now(war_points.PARIS_TZ))
        default_day = today or 1
        deds = db.get_user_war_deductions(self.target_user_id, db_path=self.db_path)
        await interaction.response.send_modal(MemberDeductionModal(self, default_day, deds.get(default_day, 0)))

    @discord.ui.button(label="Calculateur : ON", emoji="🧮", style=discord.ButtonStyle.success, row=0)
    async def calc_toggle(self, interaction: discord.Interaction, button: discord.ui.Button):
        p = db.get_or_create_profile(self.target_user_id, db_path=self.db_path)
        new_v = 0 if int(p.get("include_in_calc", 1)) else 1
        db.update_profile(self.target_user_id, {"include_in_calc": new_v}, db_path=self.db_path, touch_updated_at=False)
        await self.refresh(interaction)

    @discord.ui.button(label="Forge : Non", style=discord.ButtonStyle.secondary, row=1)
    async def max_forge_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        p = db.get_or_create_profile(self.target_user_id, db_path=self.db_path)
        db.update_profile(self.target_user_id, {"max_forge": 0 if int(p.get("max_forge") or 0) else 1}, db_path=self.db_path, touch_updated_at=False)
        await self.refresh(interaction)

    @discord.ui.button(label="Compétences : Non", style=discord.ButtonStyle.secondary, row=1)
    async def max_comp_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        p = db.get_or_create_profile(self.target_user_id, db_path=self.db_path)
        db.update_profile(self.target_user_id, {"max_competences": 0 if int(p.get("max_competences") or 0) else 1}, db_path=self.db_path, touch_updated_at=False)
        await self.refresh(interaction)

    @discord.ui.button(label="Montures : Non", style=discord.ButtonStyle.secondary, row=1)
    async def max_mount_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        p = db.get_or_create_profile(self.target_user_id, db_path=self.db_path)
        db.update_profile(self.target_user_id, {"max_montures": 0 if int(p.get("max_montures") or 0) else 1}, db_path=self.db_path, touch_updated_at=False)
        await self.refresh(interaction)

    @discord.ui.button(label="Œufs : Non", style=discord.ButtonStyle.secondary, row=1)
    async def max_egg_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        p = db.get_or_create_profile(self.target_user_id, db_path=self.db_path)
        db.update_profile(self.target_user_id, {"max_oeufs": 0 if int(p.get("max_oeufs") or 0) else 1}, db_path=self.db_path, touch_updated_at=False)
        await self.refresh(interaction)
