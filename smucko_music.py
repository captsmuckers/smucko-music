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
        logging.FileHandler(f"{DATA_DIR}/bot_logs.txt", encoding='utf-8'), 
        logging.StreamHandler(sys.stdout) 
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
dynamic_playlists = [] 

# --- 4. DISCORD BOT INITIALIZATION ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --- 5. UI COMPONENTS (Selects, Buttons, Modals) ---

class GenreSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=g, emoji="📻") for g in dynamic_genres]
        super().__init__(
            placeholder="Choose a Genre...", 
            options=options, 
            min_values=1, 
            max_values=1, 
            row=1,
            custom_id="persistent_genre_select" 
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        genre_name = self.values[0]
        try:
            tracks = plex.library.section('Music').search(genre=genre_name, libtype='track')
            if not tracks: return await interaction.followup.send("No tracks found.", ephemeral=True)
            random.shuffle(tracks)
            await start_playback_sequence(interaction, tracks, f"📻 {genre_name} Radio")
        except: await interaction.followup.send("Error searching genres.", ephemeral=True)

class PlaylistSelect(discord.ui.Select):
    def __init__(self):
        if not dynamic_playlists:
            options = [discord.SelectOption(label="No playlists found", value="none")]
        else:
            options = [discord.SelectOption(label=p[0], value=p[1], emoji="💽") for p in dynamic_playlists]
            
        super().__init__(
            placeholder="Choose a Playlist...", 
            options=options, 
            min_values=1, 
            max_values=1, 
            row=3, 
            custom_id="persistent_playlist_select"
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        playlist_id = self.values[0]
        
        if playlist_id == "none":
            return await interaction.followup.send("No playlists available on this Plex server.", ephemeral=True)
        
        try:
            selected_playlist = plex.fetchItem(int(playlist_id))
            tracks = selected_playlist.items()
            await start_playback_sequence(interaction, tracks, f"Playlist: {selected_playlist.title}")
        except Exception as e:
            logger.error(f"Playlist select error: {e}")
            await interaction.followup.send("Error loading playlist.", ephemeral=True)

class ArtistSelectionView(discord.ui.View):
    def __init__(self, artist, guild_id):
        super().__init__(timeout=60)
        self.artist = artist
        self.guild_id = guild_id

    @discord.ui.button(label="Entire Discography", style=discord.ButtonStyle.blurple)
    async def play_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        tracks = self.artist.tracks()
        random.shuffle(tracks)
        await start_playback_sequence(interaction, tracks, f"Discography: {self.artist.title}")

    @discord.ui.button(label="Artist Radio", style=discord.ButtonStyle.green)
    async def play_radio(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            station = self.artist.station()
            if station:
                from plexapi.playqueue import PlayQueue
                pq = PlayQueue.fromStationKey(plex, station.key)
                if pq.items:
                    radio_tracks = pq.items
                    await start_playback_sequence(interaction, radio_tracks, f"Radio: {self.artist.title} Station")
                    return
            
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

    @discord.ui.button(label="Pick an Album", style=discord.ButtonStyle.gray)
    async def pick_album(self, interaction: discord.Interaction, button: discord.ui.Button):
        albums = self.artist.albums()
        if not albums:
            return await interaction.response.send_message("No albums found.", ephemeral=True)
        
        view = discord.ui.View()
        select = discord.ui.Select(placeholder="Choose an album...")
        
        for album in albums[:25]:
            select.add_option(
                label=album.title[:100], 
                value=str(album.ratingKey), 
                description=f"{album.year or 'Unknown'}"
            )

        async def album_callback(int_select: discord.Interaction):
            await int_select.response.defer(ephemeral=True)
            album_id = int(select.values[0]) 
            selected_album = plex.fetchItem(album_id)
            album_tracks = selected_album.tracks()
            
            song_view = discord.ui.View()
            song_select = discord.ui.Select(placeholder=f"Pick a song (or play all)...")
            
            song_select.add_option(label="-- Play Entire Album --", value="ALL", description=f"Plays all tracks in {selected_album.title}")
            
            for track in album_tracks[:24]:
                song_select.add_option(
                    label=f"{track.trackNumber}. {track.title}"[:100],
                    value=str(track.ratingKey)
                )

            async def song_callback(int_song: discord.Interaction):
                await int_song.response.defer(ephemeral=True)
                if song_select.values[0] == "ALL":
                    await start_playback_sequence(int_song, album_tracks, f"Album: {selected_album.title}")
                else:
                    selected_track_id = int(song_select.values[0])
                    start_index = next((i for i, t in enumerate(album_tracks) if t.ratingKey == selected_track_id), 0)
                    ordered_tracks = album_tracks[start_index:]
                    await start_playback_sequence(int_song, ordered_tracks, f"🎵 {ordered_tracks[0].title}")

            song_select.callback = song_callback
            song_view.add_item(song_select)
            await int_select.edit_original_response(content=f"**{selected_album.title}** by {self.artist.title}:", view=song_view)

        select.callback = album_callback
        view.add_item(select)
        await interaction.response.edit_message(content=f"Select an album by **{self.artist.title}**:", view=view)

class SearchModal(discord.ui.Modal, title="Search Plex Music"):
    search_query = discord.ui.TextInput(label="Song, Artist, or Album", placeholder="Enter search terms...", required=True)
    
    def __init__(self):
        super().__init__()

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            query = self.search_query.value
            
            playlists = [p for p in plex.playlists() if query.lower() in p.title.lower() and p.playlistType == 'audio']
            
            if playlists:
                selected_playlist = playlists[0]
                tracks = selected_playlist.items()
                await start_playback_sequence(interaction, tracks, f"Playlist: {selected_playlist.title}")
                return

            music_library = plex.library.section('Music')
            artists = music_library.search(title=query, libtype='artist')
            
            if artists:
                artist = artists[0]
                view = ArtistSelectionView(artist, interaction.guild.id)
                await interaction.followup.send(f"Found **{artist.title}**. What would you like to play?", view=view, ephemeral=True)
            else:
                tracks = music_library.search(title=query, libtype='track')
                if not tracks:
                    return await interaction.followup.send("No results found.", ephemeral=True)
                await start_playback_sequence(interaction, tracks[:5], f"🔍 Search: {query}")
                
        except Exception as e:
            logger.error(f"Search error: {e}")
            await interaction.followup.send("Error during search. Check Plex connection.", ephemeral=True)

class MusicControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(GenreSelect())
        self.add_item(PlaylistSelect())

    @discord.ui.button(label="Vol -", style=discord.ButtonStyle.gray, row=0, custom_id="persistent_vol_down")
    async def vol_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = interaction.guild.id
        v = max(0.0, get_stored_volume(guild_id) - 0.1)
        set_stored_volume(guild_id, v)
        if interaction.guild.voice_client and interaction.guild.voice_client.source:
            interaction.guild.voice_client.source.volume = v
        await update_live_tile(guild_id, current_track.get(guild_id))
        await interaction.response.send_message(f"🔉 {int(v*100)}%", ephemeral=True)

    @discord.ui.button(label="⏯️", style=discord.ButtonStyle.blurple, row=0, custom_id="persistent_play_pause")
    async def play_pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = interaction.guild.id
        vc = interaction.guild.voice_client
        if vc:
            if vc.is_playing(): vc.pause()
            elif vc.is_paused(): vc.resume()
            await update_live_tile(guild_id, current_track.get(guild_id))
        await interaction.response.defer()

    @discord.ui.button(label="⏭️", style=discord.ButtonStyle.gray, row=0, custom_id="persistent_skip")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild.voice_client: interaction.guild.voice_client.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Vol +", style=discord.ButtonStyle.gray, row=0, custom_id="persistent_vol_up")
    async def vol_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = interaction.guild.id
        v = min(2.0, get_stored_volume(guild_id) + 0.1)
        set_stored_volume(guild_id, v)
        if interaction.guild.voice_client and interaction.guild.voice_client.source:
            interaction.guild.voice_client.source.volume = v
        await update_live_tile(guild_id, current_track.get(guild_id))
        await interaction.response.send_message(f"🔊 {int(v*100)}%", ephemeral=True)

    @discord.ui.button(label="🔍 Search", style=discord.ButtonStyle.green, row=2, custom_id="persistent_search")
    async def search_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SearchModal())

    @discord.ui.button(label="🎲 Shuffle All", style=discord.ButtonStyle.blurple, row=2, custom_id="persistent_shuffle")
    async def shuffle_all_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        tracks = plex.library.section('Music').search(libtype='track')
        random.shuffle(tracks)
        await start_playback_sequence(interaction, tracks[:50], "🎲 Shuffling Entire Library")

    @discord.ui.button(label="🛑 Stop", style=discord.ButtonStyle.red, row=2, custom_id="persistent_stop")
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = interaction.guild.id
        if guild_id in music_queues: music_queues[guild_id].clear()
        current_track.pop(guild_id, None)
        if interaction.guild.voice_client: interaction.guild.voice_client.stop()
        await interaction.response.defer()

    @discord.ui.button(label="📜 Queue", style=discord.ButtonStyle.gray, row=2, custom_id="persistent_queue")
    async def queue_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = interaction.guild.id
        if guild_id not in current_track:
            return await interaction.response.send_message("🔇 Nothing is playing.", ephemeral=True)

        now_playing = current_track[guild_id]
        queue_list = music_queues.get(guild_id, [])
        
        header = f"🎶 **Now Playing:** {now_playing.title}\n\n**Up Next:**\n"
        lines = []
        
        for i, track in enumerate(queue_list[:15], 1): 
            lines.append(f"{i}. {track.title} - {track.originalTitle or track.grandparentTitle}")

        if not lines:
            content = header + "_Queue is empty._"
        else:
            footer = f"\n\n*+ {len(queue_list) - 15} more tracks...*" if len(queue_list) > 15 else ""
            content = header + "\n".join(lines) + footer

        if len(content) > 1900:
            content = content[:1900] + "..."

        await interaction.response.send_message(content, ephemeral=True)

    # --- NEW: Lyrics Button added directly to the control panel ---
    @discord.ui.button(label="🎤 Lyrics", style=discord.ButtonStyle.blurple, row=2, custom_id="persistent_lyrics")
    async def lyrics_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id

        if guild_id not in current_track or not current_track[guild_id]:
            return await interaction.followup.send("🔇 Nothing is currently playing.", ephemeral=True)

        track = current_track[guild_id]
        artist = track.originalTitle or track.grandparentTitle
        title = track.title

        def fetch_plex_lyrics():
            try:
                track.reload()
                streams = track.lyrics()
                if not streams:
                    return None
                
                stream = streams[0]
                url = plex.url(stream.key)
                response = plex._session.get(url)
                return response.text, stream.format
            except Exception as e:
                logger.error(f"Plex lyric error: {e}")
                return None

        result = await asyncio.to_thread(fetch_plex_lyrics)

        if not result:
            return await interaction.followup.send(f"Plex doesn't have lyrics for **{title}** by **{artist}**.", ephemeral=True)
            
        lyrics_text, fmt = result

        if fmt == 'lrc' or '[' in lyrics_text:
            lyrics_text = re.sub(r'\[\d{2}:\d{2}\.\d{2,3}\]', '', lyrics_text)
            lyrics_text = "\n".join([line.strip() for line in lyrics_text.splitlines() if line.strip()])

        if len(lyrics_text) > 4000:
            lyrics_text = lyrics_text[:4000] + "\n\n... [Lyrics Truncated due to length]"
            
        embed = discord.Embed(
            title=f"🎤 {title}", 
            description=lyrics_text, 
            color=discord.Color.blue()
        )
        embed.set_author(name=artist)
        embed.set_footer(text="Lyrics provided by Plex")
        
        await interaction.followup.send(embed=embed, ephemeral=True)


# --- 6. CORE MUSIC LOGIC ---

async def update_live_tile(guild_id, track, channel=None):
    vol = get_stored_volume(guild_id)
    queue = music_queues.get(guild_id, [])
    view = MusicControlView()
    
    if not track:
        embed = discord.Embed(
            title="⏹️ Playback Stopped", 
            description="Queue cleared or music finished.",
            color=discord.Color.red()
        )
        embed.set_footer(text=f"Ready for new music | Vol: {int(vol*100)}%")
    
    else:
        embed = discord.Embed(title=f"🎧 {track.title}", color=discord.Color.green())
        embed.add_field(name="Artist", value=track.originalTitle or track.grandparentTitle, inline=True)
        embed.add_field(name="Album", value=track.parentTitle, inline=True)
        
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
        tags = music_library.listFilterChoices('genre')
        if tags:
            found_genres = sorted([t.title for t in tags if t.title])[:25]
            dynamic_genres = found_genres
            logger.info(f"Synced {len(dynamic_genres)} genres from Plex.")
        else:
            logger.warning("No genres found, using defaults.")
    except Exception as e:
        logger.error(f"Genre Sync Error: {e}")
        dynamic_genres = ["Rock", "Pop", "Jazz", "Electronic", "Classical"]

async def refresh_playlists():
    global dynamic_playlists
    try:
        audio_playlists = [p for p in plex.playlists() if p.playlistType == 'audio']
        dynamic_playlists = [(p.title[:90], str(p.ratingKey)) for p in audio_playlists[:25]]
        logger.info(f"Synced {len(dynamic_playlists)} playlists from Plex.")
    except Exception as e:
        logger.error(f"Playlist Sync Error: {e}")
        dynamic_playlists = []

# --- 7. SLASH COMMANDS ---

@bot.tree.command(name="play", description="Search and play a song from Plex")
async def play(interaction: discord.Interaction, search: str):
    await interaction.response.defer(ephemeral=True)
    tracks = plex.library.section('Music').search(search, libtype='track')
    if not tracks: 
        return await interaction.followup.send("No tracks found.", ephemeral=True)
    await start_playback_sequence(interaction, tracks, f"Playing: {search}")

@bot.tree.command(name="music", description="Summon a fresh music control panel")
async def music(interaction: discord.Interaction):
    """Opens the control tile and moves it to the bottom of the chat"""
    await interaction.response.defer(ephemeral=True)
    
    guild_id = interaction.guild.id
    
    if guild_id in last_message:
        try:
            await last_message[guild_id].delete()
        except:
            pass 
        last_message.pop(guild_id, None)
    
    if guild_id in current_track:
        await update_live_tile(guild_id, current_track[guild_id], interaction.channel)
    else:
        await update_live_tile(guild_id, None, interaction.channel)
    
    await interaction.followup.send("Control panel moved to the bottom.", ephemeral=True)

@bot.tree.command(name="clear", description="Clear the music queue and stop playback")
async def clear(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    
    if guild_id in music_queues:
        music_queues[guild_id].clear()
    
    if interaction.guild.voice_client:
        interaction.guild.voice_client.stop()
        
    current_track.pop(guild_id, None)
    
    await interaction.response.send_message("🧹 **Queue cleared and playback stopped.**", ephemeral=True)
    await update_live_tile(guild_id, None)

@bot.tree.command(name="queue", description="See the list of upcoming songs")
async def queue(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    
    if guild_id not in current_track:
        return await interaction.response.send_message("🔇 Nothing is currently playing.", ephemeral=True)

    now_playing = current_track[guild_id]
    queue_list = music_queues.get(guild_id, [])
    
    msg = f"**Now Playing:** {now_playing.title} - {now_playing.originalTitle or now_playing.grandparentTitle}\n\n"
    msg += "**Up Next:**\n"

    if not queue_list:
        msg += "_Queue is empty._"
    else:
        for i, track in enumerate(queue_list[:10], 1):
            artist = track.originalTitle or track.grandparentTitle
            msg += f"{i}. **{track.title}** - {artist}\n"
        
        if len(queue_list) > 10:
            msg += f"\n*...and {len(queue_list) - 10} more tracks.*"

    await interaction.response.send_message(msg, ephemeral=True)

# --- 8. BOT EVENTS ---

@bot.event
async def on_ready():
    logger.info(f"--- 🚀 Logged in as {bot.user.name} ---")
    
    try:
        synced = await bot.tree.sync()
        logger.info(f"Successfully synced {len(synced)} slash commands.")
    except Exception as e:
        logger.error(f"Failed to sync slash commands: {e}")
        
    await refresh_genres()
    await refresh_playlists() 
    
    bot.add_view(MusicControlView())
    logger.info("Persistent views loaded.")

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