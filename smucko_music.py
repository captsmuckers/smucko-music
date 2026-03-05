import discord
import os
import re
import random
import asyncio
import sqlite3
import logging
from datetime import datetime
from dotenv import load_dotenv
from discord.ext import commands
from discord import app_commands
from plexapi.server import PlexServer

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
        logging.FileHandler(f"{DATA_DIR}/bot_logs.txt"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('smucko-music')

# --- 2. DATABASE LOGIC ---
db_path = f"{DATA_DIR}/settings.db"

def init_db():
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS server_settings 
                 (guild_id TEXT PRIMARY KEY, volume REAL)''')
    conn.commit()
    conn.close()

def get_stored_volume(guild_id):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT volume FROM server_settings WHERE guild_id=?", (str(guild_id),))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 1.0

def set_stored_volume(guild_id, vol):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO server_settings (guild_id, volume) VALUES (?, ?)", (str(guild_id), vol))
    conn.commit()
    conn.close()

# --- 3. BOT INITIALIZATION ---
intents = discord.Intents.default()
intents.message_content = True 
intents.members = True          
bot = commands.Bot(command_prefix="!", intents=intents)

# --- 4. MUSIC VARIABLES & LOGIC ---
music_queues = {}
current_track = {} 
play_history = {} 
last_message = {} 
dynamic_genres = ["Rock", "Pop", "Jazz"] 

async def update_live_tile(guild_id, track, channel=None):
    if not track: return
    vol = get_stored_volume(guild_id)
    embed = discord.Embed(title=f"🎧 {track.title}", color=discord.Color.green())
    embed.add_field(name="Artist", value=track.originalTitle or track.grandparentTitle, inline=False)
    embed.set_footer(text=f"Vol: {int(vol*100)}% | Queue: {len(music_queues.get(guild_id, []))} left")
    # ... (Rest of your message edit logic here)

# --- 5. THE STARTUP SEQUENCE (MUST BE AT THE VERY BOTTOM) ---
def start_bot():
    print("--- 🏁 Script Starting ---")
    init_db()
    
    print(f"Connecting to Plex at: {PLEX_URL}")
    try:
        global plex
        plex = PlexServer(PLEX_URL, PLEX_TOKEN)
        print("✅ Plex Connected!")
    except Exception as e:
        print(f"❌ Plex Connection Failed: {e}")
        return

    print("Connecting to Discord...")
    bot.run(DISCORD_TOKEN)

if __name__ == "__main__":
    start_bot()