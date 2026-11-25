import asyncio
import os
from typing import Dict, Optional
import discord
from discord import app_commands
from discord.ext import commands

import admin_ui
import ai_assistant
import db
import profile_ui
import scanner
import war_points


def load_token() -> str:
    try:
        import credential as cred
        if getattr(cred, "token", None):
            return cred.token
    except ImportError:
        pass
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "Token Discord introuvable : crée un fichier credential.py (token = '...') ou définis DISCORD_TOKEN."
        )
    return token


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
emoji_map: Dict[str, discord.Emoji] = {}

AUTO_SCAN_CHANNEL_ID = 1522979872040030259


@bot.tree.command(name="profil", description="Affiche et paramètre ton profil Forge Master")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(membre="Afficher le profil d'un autre membre du clan (optionnel)")
async def profil(interaction: discord.Interaction, membre: Optional[discord.User] = None):
    if not await admin_ui.check_admin_access(interaction):
        return
    target = membre or interaction.user
    can_edit = target.id == interaction.user.id
    profile = db.get_or_create_profile(target.id, target.display_name)
    embed = profile_ui.build_profile_embed(profile, target.display_name, emoji_map, can_edit=can_edit)

    if can_edit:
        view = profile_ui.ProfileView(target.id, emoji_map)
        await interaction.response.send_message(embed=embed, view=view)
    else:
        await interaction.response.send_message(embed=embed)


FIELD_LABELS = {
    "marteaux": ("Marteaux", "fm_hammer"),
    "competences": ("Compétences", "fm_ticket"),
    "cles_monture": ("Clés Monture", "fm_mountkey"),
    "potions": ("Potions", "fm_potion"),
    "fioles_vertes": ("Fioles vertes", "fm_greenvial"),
    "oeufs": ("Œufs", "fm_egg"),
    "fusion_montures": ("Fusion Montures", "fm_mount_merge"),
    "fusion_pets": ("Fusion Pets", "fm_pet_merge"),
}


def build_scan_preview_embed(visible: Dict[str, object], has_secret: bool = False) -> discord.Embed:
    lines = []
    for key, val in visible.items():
        if key == "puissance":
            lines.append(f"• ⚔️ **Puissance :** `{val}`")
            continue
        lbl, icon_key = FIELD_LABELS.get(key, (key, "fm_hammer"))
        em = profile_ui.emoji_str(emoji_map, icon_key)
        lines.append(f"• {em} **{lbl} :** `{db.format_amount(int(val))}`")

    if not lines and has_secret:
        lines.append("• Calibration interne détectée (enregistrée en secret).")

    embed = discord.Embed(
        title="Aperçu du scan",
        color=discord.Color.from_rgb(52, 152, 219),
        description="\u200b\n" + "\n".join(lines),
    )
    embed.set_footer(text="Vérifie les valeurs détectées puis clique sur Appliquer ou Correction.")
    return embed


class ScanCorrectionModal(discord.ui.Modal):
    def __init__(self, view: "ScanConfirmView"):
        super().__init__(title="Corriger les valeurs scannées")
        self.scan_view = view
        self.inputs: Dict[str, discord.ui.TextInput] = {}
        for key in list(view.visible.keys())[:5]:
            val = view.visible[key]
            lbl = "Puissance" if key == "puissance" else FIELD_LABELS.get(key, (key, ""))[0]
            ti = discord.ui.TextInput(
                label=lbl,
                default=str(val),
                required=True,
                max_length=25,
            )
            self.inputs[key] = ti
            self.add_item(ti)

    async def on_submit(self, interaction: discord.Interaction):
        new_visible = dict(self.scan_view.visible)
        try:
            for key, ti in self.inputs.items():
                if key == "puissance":
                    new_visible[key] = db.normalize_power(ti.value)
                else:
                    new_visible[key] = db.parse_amount(ti.value)
        except ValueError as err:
            return await interaction.response.send_message(f"❌ {err}", ephemeral=True)

        self.scan_view.visible = new_visible
        embed = build_scan_preview_embed(self.scan_view.visible, has_secret=bool(self.scan_view.secret))
        await interaction.response.edit_message(embed=embed, view=self.scan_view)


class ScanConfirmView(discord.ui.View):
    def __init__(self, user_id: int, visible: Dict[str, object], secret: Dict[str, object]):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.visible = visible
        self.secret = secret
        if not visible:
            self.correct.disabled = True

    @discord.ui.button(label="Appliquer au profil", emoji="✅", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ Ce scan ne t'appartient pas.", ephemeral=True)
        payload = {**self.visible, **self.secret, "pseudo": interaction.user.display_name}
        profile = db.update_profile(self.user_id, payload, clear_deductions=True)
        embed = profile_ui.build_profile_embed(profile, interaction.user.display_name, emoji_map, can_edit=True)
        view = profile_ui.ProfileView(self.user_id, emoji_map)
        self.stop()
        await interaction.response.edit_message(content="✅ Profil mis à jour via `/scan` !", embed=embed, view=view)

    @discord.ui.button(label="Correction", emoji="✏️", style=discord.ButtonStyle.primary)
    async def correct(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ Ce scan ne t'appartient pas.", ephemeral=True)
        await interaction.response.send_modal(ScanCorrectionModal(self))

    @discord.ui.button(label="Annuler", emoji="❌", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ Ce scan ne t'appartient pas.", ephemeral=True)
        self.stop()
        await interaction.response.edit_message(content="❌ Scan annulé.", embed=None, view=None)


@bot.tree.command(name="scan", description="Détecte automatiquement tes ressources Forge Master depuis tes captures (jusqu'à 10)")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    image1="Capture d'écran Forge Master (obligatoire)",
    image2="2e capture (optionnel)",
    image3="3e capture (optionnel)",
    image4="4e capture (optionnel)",
    image5="5e capture (optionnel)",
    image6="6e capture (optionnel)",
    image7="7e capture (optionnel)",
    image8="8e capture (optionnel)",
    image9="9e capture (optionnel)",
    image10="10e capture (optionnel)",
)
async def scan(
    interaction: discord.Interaction,
    image1: discord.Attachment,
    image2: Optional[discord.Attachment] = None,
    image3: Optional[discord.Attachment] = None,
    image4: Optional[discord.Attachment] = None,
    image5: Optional[discord.Attachment] = None,
    image6: Optional[discord.Attachment] = None,
    image7: Optional[discord.Attachment] = None,
    image8: Optional[discord.Attachment] = None,
    image9: Optional[discord.Attachment] = None,
    image10: Optional[discord.Attachment] = None,
):
    await interaction.response.defer(ephemeral=True, thinking=True)
    attachments = [
        a for a in (image1, image2, image3, image4, image5, image6, image7, image8, image9, image10)
        if a is not None
    ]

    merged_visible: Dict[str, object] = {}
    merged_secret: Dict[str, object] = {}

    for att in attachments:
        if att.content_type and not att.content_type.startswith("image/"):
            continue
        try:
            raw = await att.read()
            result = await asyncio.to_thread(scanner.scan_image_bytes, raw)
        except Exception:
            continue
        merged_visible.update(result.get("visible", {}))
        merged_secret.update(result.get("secret", {}))

    if not merged_visible and not merged_secret:
        return await interaction.followup.send(
            "❌ Aucune donnée Forge Master reconnue sur ces captures. "
            "Envoie un écran principal (Forge), Probabilités Forge, Montures, Animaux, Compétences, Arbre Technologique ou Fusion.",
            ephemeral=True,
        )

    embed = build_scan_preview_embed(merged_visible, has_secret=bool(merged_secret))
    view = ScanConfirmView(interaction.user.id, merged_visible, merged_secret)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


WAR_DAY_CHOICES = [
    app_commands.Choice(name="Jour 1 (Mardi)", value=1),
    app_commands.Choice(name="Jour 2 (Mercredi)", value=2),
    app_commands.Choice(name="Jour 3 (Jeudi)", value=3),
    app_commands.Choice(name="Jour 4 (Vendredi)", value=4),
    app_commands.Choice(name="Jour 5 (Samedi)", value=5),
]


@bot.tree.command(name="snipe", description="Indique si tu peux sniper un jour de guerre (1 à 5)")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(jour="Jour de guerre (1 = Mardi ... 5 = Samedi)")
@app_commands.choices(jour=WAR_DAY_CHOICES)
async def snipe(interaction: discord.Interaction, jour: int):
    db.get_or_create_profile(interaction.user.id, interaction.user.display_name)
    active = db.toggle_snipe(interaction.user.id, jour)
    day_name = war_points.WAR_DAY_NAMES.get(jour, "")
    if active:
        await interaction.response.send_message(
            f"🎯 Tu es inscrit comme **Sniper** pour le **Jour {jour} ({day_name})**.",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            f"❌ Tu n'es plus inscrit comme **Sniper** pour le **Jour {jour} ({day_name})**.",
            ephemeral=True,
        )


WAR_MODE_CHOICES = [
    app_commands.Choice(name="⚔️ Normal (ressources Max exclues)", value="normal"),
    app_commands.Choice(name="✨ Ascension (ressources Max uniquement)", value="ascension"),
]


@bot.tree.command(name="war", description="Affiche les points potentiels du clan, déductions, snipers et hebdo")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    jour="Jour de guerre (1 = Mardi ... 5 = Samedi, par défaut aujourd'hui)",
    mode="Mode de calcul (Normal par défaut, ou Ascension)",
)
@app_commands.choices(jour=WAR_DAY_CHOICES, mode=WAR_MODE_CHOICES)
async def war(interaction: discord.Interaction, jour: Optional[int] = None, mode: Optional[str] = None):
    if jour is None:
        today_day, _ = war_points.get_day_info(war_points.datetime.now(war_points.PARIS_TZ))
        jour = today_day or 1
    profiles = db.get_all_profiles()
    sniper_ids = db.get_snipers(jour)
    deductions = db.get_war_deductions(jour)
    clan_name = interaction.guild.name if interaction.guild else "Baguette6"
    embed = profile_ui.build_war_embed(
        jour,
        profiles,
        sniper_ids,
        emoji_map,
        clan_name=clan_name,
        deductions=deductions,
        resource_filter="all",
        ascension_only=(mode == "ascension"),
    )
    await interaction.response.send_message(embed=embed, view=profile_ui.WarView(lambda: emoji_map))


@bot.tree.command(name="admin", description="Ouvre le Panel Admin Stratégie de Guerre (format PC)")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def admin_panel(interaction: discord.Interaction):
    if not await admin_ui.check_admin_access(interaction):
        return
    guild_id = interaction.guild.id if interaction.guild else 0
    clan_name = interaction.guild.name if interaction.guild else "Baguette6"
    view = admin_ui.AdminPanelView(guild_id, clan_name, emoji_map)
    await interaction.response.send_message(embed=view.current_embed(), view=view)


@bot.tree.command(name="membre", description="Gérer un membre (snipe, ressources Max/Ascension, calculateur)")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(identifiant="ID court (ex: 1), pseudo ou @mention du membre")
async def membre(interaction: discord.Interaction, identifiant: str):
    if not await admin_ui.check_admin_access(interaction):
        return
    profile = db.get_profile_by_identifier(identifiant)
    if not profile:
        return await interaction.response.send_message(
            f"❌ Aucun membre trouvé pour `{identifiant}`. Consulte la liste via `/admin` → **👥 Membres**.",
            ephemeral=True,
        )
    uid = int(profile["user_id"])
    sdays = db.get_user_snipe_days(uid)
    deds = db.get_user_war_deductions(uid)
    embed = admin_ui.build_member_admin_embed(profile, sdays, deductions=deds)
    view = admin_ui.MemberEditView(uid)
    await interaction.response.send_message(embed=embed, view=view)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if message.channel.id == AUTO_SCAN_CHANNEL_ID:
        merged_visible: Dict[str, object] = {}
        merged_secret: Dict[str, object] = {}

        if message.content and message.content.strip():
            text_res = scanner.parse_profile_text(message.content)
            merged_visible.update(text_res.get("visible", {}))
            merged_secret.update(text_res.get("secret", {}))

        imgs = [a for a in message.attachments if not a.content_type or a.content_type.startswith("image/")]
        for att in imgs[:10]:
            try:
                raw = await att.read()
                result = await asyncio.to_thread(scanner.scan_image_bytes, raw)
            except Exception:
                continue
            merged_visible.update(result.get("visible", {}))
            merged_secret.update(result.get("secret", {}))

        if merged_visible or merged_secret:
            payload = {**merged_visible, **merged_secret, "pseudo": message.author.display_name}
            db.update_profile(message.author.id, payload, clear_deductions=True)
            embed = discord.Embed(
                description="Profil mis à jour avec succès",
                color=discord.Color.from_rgb(46, 204, 113),
            )
            try:
                await message.reply(embed=embed, mention_author=False)
            except discord.HTTPException:
                pass
        return

    if message.channel.id == ai_assistant.AI_CHANNEL_ID and message.content.strip():
        # Replace raw <@id> mentions with readable @DisplayName + keep ID hint
        clean_prompt = message.clean_content.strip()
        async with message.channel.typing():
            reply = await ai_assistant.ask_local_ai(
                clean_prompt,
                author_name=message.author.display_name,
            )
        # Chunk response cleanly under Discord's 2000-char limit
        for i in range(0, len(reply), 1950):
            await message.reply(reply[i : i + 1950], mention_author=False)
        return
    await bot.process_commands(message)


@bot.event
async def on_ready():
    global emoji_map
    db.init_db()
    emoji_map = await profile_ui.sync_application_emojis(bot)
    bot.add_view(profile_ui.WarView(lambda: emoji_map))
    print(f"Connecté en tant que {bot.user} ({len(emoji_map)}/{len(profile_ui.EMOJI_SPECS)} assets chargés)")
    try:
        synced = await bot.tree.sync()
        print(f"{len(synced)} commande(s) synchronisée(s)")
    except Exception as e:
        print(f"Erreur de synchronisation : {e}")


def main():
    db.init_db()
    bot.run(load_token())


if __name__ == "__main__":
    main()
