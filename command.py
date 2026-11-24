import time
import discord
from discord.ext import commands
import credential as cred

token = cred.token
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.tree.command()
async def multiplication(interaction: discord.Interaction, a: int, b: int):
  await interaction.response.send_message(f"{a} x {b} = {a * b}")

@bot.event
async def on_ready():
  print(f"Connecté en tant que {bot.user}")
  try:
    synced = await bot.tree.sync()
    print(f"{len(synced)} commande(s) synchronisée(s)")
  except Exception as e:
    print(e)

def main():
  bot.run(token)

if __name__ == '__main__':
  main()