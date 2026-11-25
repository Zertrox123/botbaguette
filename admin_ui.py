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

    # Compact 50-char table so it never wraps inside Discord PC embeds
    table_lines = [
        f"{'Jour':<11} | {'Clan Pts':>9} | {'Snipers':>13} | {'Déduits':>7}",
        "-" * 49,
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
            ded_sum += ded
            pts, _ = war_points.compute_day_potential(
                p, d, resource_filter=res_filter, ascension_only=ascension_only, deducted_pts=ded
            )
            clan_pts += pts
            if uid in snipers:
                snipe_pts += pts
        d_label = f"J{d} {war_points.WAR_DAY_NAMES[d]}"
        snipe_col = f"{db.format_amount(snipe_pts)} ({len(snipers)})"
        ded_col = f"-{db.format_amount(ded_sum)}" if ded_sum > 0 else "0"
        table_lines.append(
            f"{d_label:<11} | {db.format_amount(clan_pts):>9} | {snipe_col:>13} | {ded_col:>7}"
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
        pts, _ = war_points.compute_day_potential(
            p, detail_day, resource_filter=res_filter, ascension_only=ascension_only, deducted_pts=ded
        )
        ranked.append((pts, p, uid in detail_snipers, ded))
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
        panel_view = AdminPanelView(self.guild_id, self.clan_name, self.emoji_map, db_path=self.db_path)
        embed = build_admin_panel_embed(self.guild_id, self.clan_name, self.emoji_map, db_path=self.db_path)
        await interaction.response.edit_message(embed=embed, view=panel_view)


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

        res_options = [
            discord.SelectOption(label=lbl, value=k, default=(k == self.cur_res))
            for k, lbl in war_points.RESOURCE_FILTER_LABELS.items()
        ]
        res_select = discord.ui.Select(
            placeholder="Choisir la ressource à calculer",
            options=res_options,
            row=0,
        )
        res_select.callback = self._on_res_change
        self.add_item(res_select)

        day_options = [
            discord.SelectOption(label="Vue Globale (J1 → J5)", value="0", default=(self.cur_day == 0)),
            discord.SelectOption(label="Jour 1 — Mardi", value="1", default=(self.cur_day == 1)),
            discord.SelectOption(label="Jour 2 — Mercredi", value="2", default=(self.cur_day == 2)),
            discord.SelectOption(label="Jour 3 — Jeudi", value="3", default=(self.cur_day == 3)),
            discord.SelectOption(label="Jour 4 — Vendredi", value="4", default=(self.cur_day == 4)),
            discord.SelectOption(label="Jour 5 — Samedi", value="5", default=(self.cur_day == 5)),
        ]
        day_select = discord.ui.Select(
            placeholder="Choisir le jour de guerre",
            options=day_options,
            row=1,
        )
        day_select.callback = self._on_day_change
        self.add_item(day_select)

    async def _on_res_change(self, interaction: discord.Interaction):
        self.cur_res = interaction.data["values"][0]
        db.update_admin_state(self.guild_id, {"resource_filter": self.cur_res}, db_path=self.db_path)
        embed = build_admin_panel_embed(self.guild_id, self.clan_name, self.emoji_map, db_path=self.db_path)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_day_change(self, interaction: discord.Interaction):
        self.cur_day = int(interaction.data["values"][0])
        db.update_admin_state(self.guild_id, {"selected_day": self.cur_day}, db_path=self.db_path)
        embed = build_admin_panel_embed(self.guild_id, self.clan_name, self.emoji_map, db_path=self.db_path)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Valider & Retour Panel", emoji="✅", style=discord.ButtonStyle.success, row=2)
    async def done(self, interaction: discord.Interaction, button: discord.ui.Button):
        panel_view = AdminPanelView(self.guild_id, self.clan_name, self.emoji_map, db_path=self.db_path)
        embed = build_admin_panel_embed(self.guild_id, self.clan_name, self.emoji_map, db_path=self.db_path)
        await interaction.response.edit_message(embed=embed, view=panel_view)


class AdminPanelView(discord.ui.View):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_admin_access(interaction)

    def __init__(
        self,
        guild_id: int,
        clan_name: str,
        emoji_map: Dict[str, discord.Emoji],
        db_path: Optional[str] = None,
    ):
        super().__init__(timeout=600)
        self.guild_id = guild_id
        self.clan_name = clan_name
        self.emoji_map = emoji_map
        self.db_path = db_path
        state = db.get_admin_state(guild_id, db_path=db_path)
        asc_on = bool(int(state.get("ascension_only") or 0))
        self.ascension_btn.style = discord.ButtonStyle.success if asc_on else discord.ButtonStyle.secondary
        self.ascension_btn.label = "Ascension : ON" if asc_on else "Ascension"

    @discord.ui.button(label="Scan Guerre", emoji="📸", style=discord.ButtonStyle.primary, row=0)
    async def scan_war_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
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
        locked_lines = []
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
                    ok, lock_rem = db.record_war_deduction(
                        uid, eff_day, pts, db_path=self.db_path
                    )
                    if ok:
                        applied_daily.append(f"• **{matched['pseudo']}** : `-{db.format_amount(pts)} pts` (J{eff_day})")
                    else:
                        locked_lines.append(f"• **{matched['pseudo']}** : verrouillé encore `{lock_rem // 60}m{lock_rem % 60:02d}s`")

        report = [f"📊 **Résultat du Scan Guerre ({len(collected)} capture(s))**"]
        if applied_daily:
            report.append("\n**📅 Journalier (déduit du calculateur) :**\n" + "\n".join(applied_daily[:20]))
        if applied_weekly:
            report.append("\n**🗓️ Hebdomadaire (suivi semaine uniquement, non déduit du daily) :**\n" + "\n".join(applied_weekly[:20]))
        if locked_lines:
            report.append("\n**Ignorés (verrou anti-faux-positif 10min) :**\n" + "\n".join(locked_lines[:10]))
        if unmatched:
            report.append("\n**Non reconnus dans la DB :** " + ", ".join(unmatched[:10]))
        if not applied_daily and not applied_weekly and not locked_lines:
            report.append("\n❌ Aucun joueur du clan reconnu sur ces captures.")

        # Refresh original panel message
        try:
            embed = build_admin_panel_embed(self.guild_id, self.clan_name, self.emoji_map, db_path=self.db_path)
            await interaction.message.edit(embed=embed, view=self)
        except Exception:
            pass
        await interaction.followup.send("\n".join(report))

    @discord.ui.button(label="Membres", emoji="👥", style=discord.ButtonStyle.secondary, row=0)
    async def members_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = db.get_admin_state(self.guild_id, db_path=self.db_path)
        day = _get_active_day_for_details(int(state.get("selected_day") or 0))
        view = MembersPaginationView(self.guild_id, self.clan_name, self.emoji_map, snipe_day=day, db_path=self.db_path)
        await interaction.response.edit_message(embed=view._current_embed(), view=view)

    @discord.ui.button(label="Filtre", emoji="🔍", style=discord.ButtonStyle.secondary, row=0)
    async def filter_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = FilterView(self.guild_id, self.clan_name, self.emoji_map, db_path=self.db_path)
        embed = build_admin_panel_embed(self.guild_id, self.clan_name, self.emoji_map, db_path=self.db_path)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Ascension", emoji="✨", style=discord.ButtonStyle.secondary, row=0)
    async def ascension_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = db.get_admin_state(self.guild_id, db_path=self.db_path)
        new_val = 0 if int(state.get("ascension_only") or 0) else 1
        db.update_admin_state(self.guild_id, {"ascension_only": new_val}, db_path=self.db_path)
        new_view = AdminPanelView(self.guild_id, self.clan_name, self.emoji_map, db_path=self.db_path)
        embed = build_admin_panel_embed(self.guild_id, self.clan_name, self.emoji_map, db_path=self.db_path)
        await interaction.response.edit_message(embed=embed, view=new_view)

    @discord.ui.button(label="Reset Déductions", emoji="🔄", style=discord.ButtonStyle.danger, row=0)
    async def reset_ded_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = db.get_admin_state(self.guild_id, db_path=self.db_path)
        sel = int(state.get("selected_day") or 0)
        db.reset_war_deductions(war_day=(sel if 1 <= sel <= 5 else None), db_path=self.db_path)
        embed = build_admin_panel_embed(self.guild_id, self.clan_name, self.emoji_map, db_path=self.db_path)
        await interaction.response.edit_message(embed=embed, view=self)


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
