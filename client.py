import discord
import credential as cred

token = cred.token

client = discord.Client(intents=discord.Intents.all())

client.run(token =token)