import discord
import os
import re
import random
import asyncio
import sqlite3
import logging
import sys
import io
from datetime import datetime
from dotenv import load_dotenv
from discord.ext import commands
from discord import app_commands
from plexapi.server import PlexServer

# Force UTF-8 encoding for the console to prevent emoji crashes
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# --- 1. CONFIGURATION & LOGGING ---
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
PLEX_URL = os.getenv('PLEX_URL')
PLEX_TOKEN = os.getenv('PLEX_TOKEN')

DATA_DIR = "/app/data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(f"{DATA_DIR}/bot_logs.txt", encoding='utf-8'), # Added encoding here
        logging.StreamHandler(sys.stdout) # Force stream to use our UTF-8 stdout
    ]
)
logger = logging.getLogger('smucko-music')

# --- 2. DATABASE PERSISTENCE ---
db_path = f"{DATA_DIR}/settings.db"

def init_db():
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS server_settings 
                 (guild_id TEXT PRIMARY KEY, volume REAL)''')
    conn.commit()
    conn.close()

def get_stored_volume(guild_id):
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("SELECT volume FROM server_settings WHERE guild_id=?", (str(guild_id),))
        row = c.fetchone()
        conn.close()
        return row[0] if row else 1.0
    except: return 1.0

def set_stored_volume(guild_id, vol):
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO server_settings (guild_id, volume) VALUES (?, ?)", (str(guild_id), vol))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"DB Error: {e}")

# --- 3. GLOBAL VARIABLES ---
music_queues = {}
current_track = {} 
play_history = {} 
last_message = {} 
dynamic_genres = ["Rock", "Pop", "Jazz"] 

# --- 4. DISCORD BOT INITIALIZATION ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
# We use commands.Bot to access the command tree, but we use Slash Commands
bot = commands.Bot(command_prefix="!", intents=intents)

# --- 5. UI COMPONENTS (Selects, Buttons, Modals) ---

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

class ArtistSelectionView(discord.ui.View):
    def __init__(self, artist, guild_id):
        super().__init__(timeout=60)
        self.artist = artist
        self.guild_id = guild_id

    # --- CHOICE 1: ENTIRE DISCOGRAPHY ---
    @discord.ui.button(label="Entire Discography", style=discord.ButtonStyle.blurple)
    async def play_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        tracks = self.artist.tracks()
        random.shuffle(tracks)
        await start_playback_sequence(interaction, tracks, f"Discography: {self.artist.title}")

    # --- CHOICE 2: NATIVE PLEX ARTIST RADIO ---
    @discord.ui.button(label="Artist Radio", style=discord.ButtonStyle.green)
    async def play_radio(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            # 1. Ask Plex for the specific 'Station' for this artist
            station = self.artist.station()
            
            if station:
                from plexapi.playqueue import PlayQueue
                # 2. Generate a PlayQueue from that station
                # This uses Plex's native discovery/sonic analysis
                pq = PlayQueue.fromStationKey(plex, station.key)
                
                if pq.items:
                    # We take the items Plex provided (usually about 50 tracks)
                    radio_tracks = pq.items
                    await start_playback_sequence(interaction, radio_tracks, f"Radio: {self.artist.title} Station")
                    return
            
            # 3. FALLBACK: If Plex Station isn't available, use Genre Radio
            logger.warning(f"Native station unavailable for {self.artist.title}. Using genre fallback.")
            genres = [g.tag for g in self.artist.genres]
            if genres:
                music_lib = plex.library.section('Music')
                fallback_tracks = music_lib.search(genre=genres[0], libtype='track')
                random.shuffle(fallback_tracks)
                await start_playback_sequence(interaction, fallback_tracks[:50], f"Genre Radio: {genres[0]}")
            else:
                await interaction.followup.send("Plex Radio is unavailable for this artist.", ephemeral=True)

        except Exception as e:
            logger.error(f"Radio error: {e}")
            await interaction.followup.send(f"Error starting Plex Radio: {e}", ephemeral=True)

    # --- CHOICE 3: PICK AN ALBUM ---
    @discord.ui.button(label="Pick an Album", style=discord.ButtonStyle.gray)
    async def pick_album(self, interaction: discord.Interaction, button: discord.ui.Button):
        # ... (keep your existing pick_album code here) ...

class SearchModal(discord.ui.Modal, title="Search Plex Music"):
    search_query = discord.ui.TextInput(label="Song, Artist, or Album", placeholder="Enter search terms...", required=True)
    
    def __init__(self, guild_id):
        super().__init__()
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            music_library = plex.library.section('Music')
            query = self.search_query.value
            
            # Look for an exact Artist match
            artists = music_library.search(title=query, libtype='artist')
            
            if artists:
                # If we found an artist (like Kendrick Lamar), show the NEW menu
                artist = artists[0]
                view = ArtistSelectionView(artist, interaction.guild.id)
                await interaction.followup.send(f"Found **{artist.title}**. What would you like to play?", view=view, ephemeral=True)
            else:
                # If no artist found, just search for tracks normally
                tracks = music_library.search(title=query, libtype='track')
                if not tracks:
                    return await interaction.followup.send("No results found.", ephemeral=True)
                await start_playback_sequence(interaction, tracks[:5], f"🔍 Search: {query}")
                
        except Exception as e:
            logger.error(f"Search error: {e}")
            await interaction.followup.send("Error during search. Check Plex connection.", ephemeral=True)

class MusicControlView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.add_item(GenreSelect(guild_id))

    @discord.ui.button(label="Vol -", style=discord.ButtonStyle.gray, row=0)
    async def vol_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        v = max(0.0, get_stored_volume(self.guild_id) - 0.1)
        set_stored_volume(self.guild_id, v)
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
        v = min(2.0, get_stored_volume(self.guild_id) + 0.1)
        set_stored_volume(self.guild_id, v)
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
        tracks = plex.library.section('Music').search(libtype='track')
        random.shuffle(tracks)
        await start_playback_sequence(interaction, tracks[:50], "🎲 Shuffling Entire Library")

    @discord.ui.button(label="🛑 Stop", style=discord.ButtonStyle.red, row=2)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.guild_id in music_queues: music_queues[self.guild_id].clear()
        current_track.pop(self.guild_id, None)
        if interaction.guild.voice_client: interaction.guild.voice_client.stop()
        await interaction.response.defer()

    @discord.ui.button(label="📜 Queue", style=discord.ButtonStyle.gray, row=2)
    async def queue_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = self.guild_id
        if guild_id not in current_track:
            return await interaction.response.send_message("🔇 Nothing is playing.", ephemeral=True)

        now_playing = current_track[guild_id]
        queue_list = music_queues.get(guild_id, [])
        
        # Build the message safely
        header = f"🎶 **Now Playing:** {now_playing.title}\n\n**Up Next:**\n"
        lines = []
        
        for i, track in enumerate(queue_list[:15], 1): # Limit to top 15
            lines.append(f"{i}. {track.title} - {track.originalTitle or track.grandparentTitle}")

        if not lines:
            content = header + "_Queue is empty._"
        else:
            footer = f"\n\n*+ {len(queue_list) - 15} more tracks...*" if len(queue_list) > 15 else ""
            content = header + "\n".join(lines) + footer

        # Final safety check: if the 15 songs are still too long, we slice even more
        if len(content) > 1900:
            content = content[:1900] + "..."

        await interaction.response.send_message(content, ephemeral=True)


# --- 6. CORE MUSIC LOGIC ---

async def update_live_tile(guild_id, track, channel=None):
    vol = get_stored_volume(guild_id)
    queue = music_queues.get(guild_id, [])
    view = MusicControlView(guild_id)
    
    # --- HANDLE STOPPED STATE ---
    if not track:
        embed = discord.Embed(
            title="⏹️ Playback Stopped", 
            description="Queue cleared or music finished.",
            color=discord.Color.red()
        )
        embed.set_footer(text=f"Ready for new music | Vol: {int(vol*100)}%")
    
    # --- HANDLE PLAYING STATE ---
    else:
        embed = discord.Embed(title=f"🎧 {track.title}", color=discord.Color.green())
        embed.add_field(name="Artist", value=track.originalTitle or track.grandparentTitle, inline=True)
        embed.add_field(name="Album", value=track.parentTitle, inline=True)
        
        # Get Next Song Name
        next_up = "End of Queue"
        if queue:
            next_track = queue[0]
            next_up = f"{next_track.title} by {next_track.originalTitle or next_track.grandparentTitle}"
        
        embed.add_field(name="⏭️ Next Up", value=next_up, inline=False)

        try:
            embed.set_thumbnail(url=track.thumbUrl)
        except: pass

        status = "⏸️ Paused"
        vc = bot.get_guild(int(guild_id)).voice_client
        if vc and vc.is_playing(): status = "🎶 Playing"

        embed.set_footer(text=f"{status} | Vol: {int(vol*100)}% | Total in Queue: {len(queue)}")

    # --- UPDATE OR SEND MESSAGE ---
    if guild_id in last_message:
        try:
            await last_message[guild_id].edit(embed=embed, view=view)
            return
        except: pass
    
    if channel:
        msg = await channel.send(embed=embed, view=view)
        last_message[guild_id] = msg

def check_queue(guild_id, vc):
    if guild_id in music_queues and music_queues[guild_id]:
        next_track = music_queues[guild_id].pop(0)
        current_track[guild_id] = next_track
        source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(next_track.getStreamURL()))
        source.volume = get_stored_volume(guild_id)
        vc.play(source, after=lambda e: check_queue(guild_id, vc))
        asyncio.run_coroutine_threadsafe(update_live_tile(guild_id, next_track), bot.loop)
    else:
        current_track.pop(guild_id, None)

async def start_playback_sequence(interaction, tracks, message):
    guild_id = interaction.guild.id
    vc = interaction.guild.voice_client or (await interaction.user.voice.channel.connect() if interaction.user.voice else None)
    if not vc: return await interaction.followup.send("Join voice!", ephemeral=True)

    if guild_id not in music_queues: music_queues[guild_id] = []
    
    if not vc.is_playing() and not vc.is_paused():
        first = tracks.pop(0)
        current_track[guild_id] = first
        music_queues[guild_id].extend(tracks)
        source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(first.getStreamURL()))
        source.volume = get_stored_volume(guild_id)
        vc.play(source, after=lambda e: check_queue(guild_id, vc))
        await update_live_tile(guild_id, first, interaction.channel)
    else:
        music_queues[guild_id].extend(tracks)
        await update_live_tile(guild_id, current_track[guild_id])
    
    await interaction.followup.send(f"✅ {message}!", ephemeral=True)

async def refresh_genres():
    global dynamic_genres
    try:
        music_library = plex.library.section('Music')
        
        # Method: Get tags from the search filter choices
        # This is the most reliable way to get the exact list Plex uses for filters
        tags = music_library.listFilterChoices('genre')
        
        if tags:
            # Sort and take top 25
            found_genres = sorted([t.title for t in tags if t.title])[:25]
            dynamic_genres = found_genres
            logger.info(f"Synced {len(dynamic_genres)} genres from Plex.")
        else:
            logger.warning("No genres found, using defaults.")
    except Exception as e:
        logger.error(f"Genre Sync Error: {e}")
        # Fallback to defaults so the Select Menu doesn't break
        dynamic_genres = ["Rock", "Pop", "Jazz", "Electronic", "Classical"]

# --- 7. SLASH COMMANDS ---

@bot.tree.command(name="play", description="Search and play a song from Plex")
async def play(interaction: discord.Interaction, search: str):
    await interaction.response.defer(ephemeral=True)
    tracks = plex.library.section('Music').search(search, libtype='track')
    if not tracks: 
        return await interaction.followup.send("No tracks found.", ephemeral=True)
    await start_playback_sequence(interaction, tracks, f"Playing: {search}")

@bot.tree.command(name="music", description="Open the music control panel")
async def music(interaction: discord.Interaction):
    """Explicitly opens the control tile"""
    await interaction.response.defer(ephemeral=True)
    # Get a random popular track to start the tile if nothing is playing
    tracks = plex.library.section('Music').search(libtype='track', limit=1)
    if tracks:
        await interaction.channel.send("🎛️ Smucko Music Control Panel", view=MusicControlView(interaction.guild.id))
        await interaction.followup.send("Control panel opened.", ephemeral=True)

@bot.tree.command(name="clear", description="Clear the music queue and stop playback")
async def clear(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    
    # 1. Clear the actual list of upcoming songs
    if guild_id in music_queues:
        music_queues[guild_id].clear()
    
    # 2. Stop the current song if something is playing
    if interaction.guild.voice_client:
        interaction.guild.voice_client.stop()
        
    # 3. Clean up the "current_track" tracker
    current_track.pop(guild_id, None)
    
    await interaction.response.send_message("🧹 **Queue cleared and playback stopped.**", ephemeral=True)
    
    # Optional: Update the live tile to show it's stopped
    await update_live_tile(guild_id, None)

@bot.tree.command(name="queue", description="See the list of upcoming songs")
async def queue(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    
    # Check if anything is playing
    if guild_id not in current_track:
        return await interaction.response.send_message("🔇 Nothing is currently playing.", ephemeral=True)

    # Start the list with the Now Playing track
    now_playing = current_track[guild_id]
    queue_list = music_queues.get(guild_id, [])
    
    msg = f"**Now Playing:** {now_playing.title} - {now_playing.originalTitle or now_playing.grandparentTitle}\n\n"
    msg += "**Up Next:**\n"

    if not queue_list:
        msg += "_Queue is empty._"
    else:
        # Grab the first 10 songs so the message doesn't get too long
        for i, track in enumerate(queue_list[:10], 1):
            artist = track.originalTitle or track.grandparentTitle
            msg += f"{i}. **{track.title}** - {artist}\n"
        
        # If there are more than 10, show a count of the remainder
        if len(queue_list) > 10:
            msg += f"\n*...and {len(queue_list) - 10} more tracks.*"

    await interaction.response.send_message(msg, ephemeral=True)

# --- 8. BOT EVENTS ---

@bot.event
async def on_ready():
    logger.info(f"--- 🚀 Logged in as {bot.user.name} ---")
    await refresh_genres()
    
    # This syncs the slash commands to Discord's servers
    try:
        synced = await bot.tree.sync()
        logger.info(f"Successfully synced {len(synced)} slash commands.")
    except Exception as e:
        logger.error(f"Failed to sync slash commands: {e}")

# --- 9. THE STARTUP SEQUENCE ---

if __name__ == "__main__":
    print("--- 🏁 Script Starting ---")
    init_db()
    
    try:
        plex = PlexServer(PLEX_URL, PLEX_TOKEN)
        print("✅ Plex Connected!")
    except Exception as e:
        print(f"❌ Plex Connection Failed: {e}")
        exit(1)

    print("Connecting to Discord...")
    if not DISCORD_TOKEN:
        print("❌ Error: No DISCORD_TOKEN found!")
    else:
        bot.run(DISCORD_TOKEN)