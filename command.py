import asyncio
import os
from typing import Dict, Optional
import discord
from discord import app_commands
from discord.ext import commands

import db
import profile_ui
import scanner


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
bot = commands.Bot(command_prefix="!", intents=intents)
emoji_map: Dict[str, discord.Emoji] = {}


@bot.tree.command(name="profil", description="Affiche et paramètre ton profil Forge Master")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(membre="Afficher le profil d'un autre membre du clan (optionnel)")
async def profil(interaction: discord.Interaction, membre: Optional[discord.User] = None):
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
    embed.set_footer(text="Vérifie les valeurs détectées puis clique sur Appliquer ou Corriger.")
    return embed


class ScanCorrectionModal(discord.ui.Modal):
    def __init__(self, view: "ScanConfirmView"):
        super().__init__(title="Corriger les valeurs scannées")
        self.scan_view = view
        self.inputs: Dict[str, discord.ui.TextInput] = {}
        # Discord modals support up to 5 TextInput fields
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
        profile = db.update_profile(self.user_id, payload)
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


@bot.tree.command(name="scan", description="Détecte automatiquement tes ressources Forge Master depuis tes captures d'écran")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    image1="Capture d'écran Forge Master (obligatoire)",
    image2="2e capture (optionnel)",
    image3="3e capture (optionnel)",
    image4="4e capture (optionnel)",
    image5="5e capture (optionnel)",
)
async def scan(
    interaction: discord.Interaction,
    image1: discord.Attachment,
    image2: Optional[discord.Attachment] = None,
    image3: Optional[discord.Attachment] = None,
    image4: Optional[discord.Attachment] = None,
    image5: Optional[discord.Attachment] = None,
):
    await interaction.response.defer(ephemeral=True, thinking=True)
    attachments = [a for a in (image1, image2, image3, image4, image5) if a is not None]

    merged_visible: Dict[str, object] = {}
    merged_secret: Dict[str, object] = {}

    for att in attachments:
        if att.content_type and not att.content_type.startswith("image/"):
            continue
        raw = await att.read()
        result = await asyncio.to_thread(scanner.scan_image_bytes, raw)
        merged_visible.update(result.get("visible", {}))
        merged_secret.update(result.get("secret", {}))

    if not merged_visible and not merged_secret:
        return await interaction.followup.send(
            "❌ Aucune donnée Forge Master reconnue sur ces captures. "
            "Envoie un écran principal (Forge), Montures, Animaux, Compétences, Arbre Technologique ou Fusion.",
            ephemeral=True,
        )

    embed = build_scan_preview_embed(merged_visible, has_secret=bool(merged_secret))
    view = ScanConfirmView(interaction.user.id, merged_visible, merged_secret)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


@bot.event
async def on_ready():
    global emoji_map
    db.init_db()
    emoji_map = await profile_ui.sync_application_emojis(bot)
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
