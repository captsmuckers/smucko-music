import discord
import os
import re
import random
import asyncio
from dotenv import load_dotenv
from discord.ext import commands
from discord import app_commands
from plexapi.server import PlexServer

# --- CONFIGURATION ---
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
PLEX_URL = os.getenv('PLEX_URL') 
PLEX_TOKEN = os.getenv('PLEX_TOKEN')

# --- INITIALIZE PLEX & DISCORD ---
plex = PlexServer(PLEX_URL, PLEX_TOKEN)

music_queues = {}
server_volumes = {} 
current_track = {} 
play_history = {} 
last_message = {} 
dynamic_genres = ["Rock", "Pop", "Jazz"] 

async def refresh_genres():
    """Queries Plex for the actual genres in your library."""
    global dynamic_genres
    try:
        music_library = plex.library.section('Music')
        tags = music_library.listTags('genre')
        found_genres = sorted([t.title for t in tags])[:25]
        if found_genres:
            dynamic_genres = found_genres
            print(f"✅ Synced {len(dynamic_genres)} genres from Plex.")
    except Exception as e:
        print(f"⚠️ Could not sync genres: {e}")

class GenreSelect(discord.ui.Select):
    def __init__(self, guild_id):
        options = [discord.SelectOption(label=g, emoji="📻") for g in dynamic_genres]
        super().__init__(placeholder="Choose a Genre...", options=options, min_values=1, max_values=1, row=1)
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        genre_name = self.values[0]
        try:
            tracks = plex.library.section('Music').search(genre=genre_name, libtype='track')
            if not tracks: return await interaction.followup.send("No tracks found.", ephemeral=True)
            random.shuffle(tracks)
            await start_playback_sequence(interaction, tracks, f"📻 {genre_name} Radio")
        except: await interaction.followup.send("Error searching genres.", ephemeral=True)

class MusicControlView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.add_item(GenreSelect(guild_id))

    @discord.ui.button(label="Vol -", style=discord.ButtonStyle.gray, row=0)
    async def vol_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        v = max(0.0, server_volumes.get(self.guild_id, 1.0) - 0.1)
        server_volumes[self.guild_id] = v
        if interaction.guild.voice_client and interaction.guild.voice_client.source:
            interaction.guild.voice_client.source.volume = v
        await update_live_tile(self.guild_id, current_track.get(self.guild_id))
        await interaction.response.send_message(f"🔉 {int(v*100)}%", ephemeral=True)

    @discord.ui.button(label="⏯️", style=discord.ButtonStyle.blurple, row=0)
    async def play_pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if vc:
            if vc.is_playing(): vc.pause()
            elif vc.is_paused(): vc.resume()
            await update_live_tile(self.guild_id, current_track.get(self.guild_id))
        await interaction.response.defer()

    @discord.ui.button(label="⏭️", style=discord.ButtonStyle.gray, row=0)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild.voice_client: interaction.guild.voice_client.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Vol +", style=discord.ButtonStyle.gray, row=0)
    async def vol_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        v = min(2.0, server_volumes.get(self.guild_id, 1.0) + 0.1)
        server_volumes[self.guild_id] = v
        if interaction.guild.voice_client and interaction.guild.voice_client.source:
            interaction.guild.voice_client.source.volume = v
        await update_live_tile(self.guild_id, current_track.get(self.guild_id))
        await interaction.response.send_message(f"🔊 {int(v*100)}%", ephemeral=True)

    @discord.ui.button(label="🔍 Search", style=discord.ButtonStyle.green, row=2)
    async def search_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SearchModal(self.guild_id))

    @discord.ui.button(label="🎲 Shuffle All", style=discord.ButtonStyle.blurple, row=2)
    async def shuffle_all_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        # Pull 50 random tracks from the whole library
        tracks = plex.library.section('Music').search(libtype='track')
        random.shuffle(tracks)
        await start_playback_sequence(interaction, tracks[:50], "🎲 Shuffling Entire Library")

    @discord.ui.button(label="🛑 Stop", style=discord.ButtonStyle.red, row=2)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.guild_id in music_queues: music_queues[self.guild_id].clear()
        current_track.pop(self.guild_id, None)
        if interaction.guild.voice_client: interaction.guild.voice_client.stop()
        await interaction.response.defer()

# --- HELPER: START PLAYBACK ---

async def start_playback_sequence(interaction, tracks, message):
    guild_id = interaction.guild.id
    vc = interaction.guild.voice_client or (await interaction.user.voice.channel.connect() if interaction.user.voice else None)
    if not vc: return await interaction.followup.send("Join voice!", ephemeral=True)

    if guild_id not in music_queues: music_queues[guild_id] = []
    
    # If nothing is playing, start immediately
    if not vc.is_playing() and not vc.is_paused():
        first = tracks.pop(0)
        current_track[guild_id] = first
        music_queues[guild_id].extend(tracks)
        source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(first.getStreamURL()))
        source.volume = server_volumes.get(guild_id, 1.0)
        vc.play(source, after=lambda e: check_queue(guild_id, vc))
        await update_live_tile(guild_id, first, interaction.channel)
    else:
        music_queues[guild_id].extend(tracks)
        await update_live_tile(guild_id, current_track[guild_id])
    
    await interaction.followup.send(f"✅ {message}!", ephemeral=True)

# --- REUSABLE SEARCH MODAL & UPDATE LOGIC (Remains the same) ---
# [Logic for SearchModal, update_live_tile, check_queue, and PlexBot class here]