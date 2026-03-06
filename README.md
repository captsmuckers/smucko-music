Smucko Music
A Discord Music Bot that turns your Plex Library into a collaborative jukebox. This bot allows you to stream your own high-quality library directly to your voice channel without relying on external streaming services.

Key Features
Artist-First Search: Search for an artist and choose to play their entire discography, a specific album, or a curated radio station.

Smart Artist Radio: Generates a mix of the artist's tracks and similar music from your library using Plex's Sonic Analysis logic with a genre-based fallback.

Live Control Tile: A dynamic, auto-updating Discord embed that shows what is playing, what is next, and provides one-click buttons for volume, skipping, and searching.

Drill-Down Selection: Navigate from Artist to Album to Specific Song using intuitive Discord dropdown menus.

Persistent Settings: Remembers server volume levels across restarts using a local SQLite database.

**Installation**

Prerequisites:
Python 3.10 or higher
FFmpeg (Required for audio streaming)
Plex Media Server (with Remote Access or local IP access)
Discord Bot Token (via Discord Developer Portal)

1. Clone the Repository
Bash
git clone https://github.com/captsmuckers/smucko-music.git
cd smucko-music
2. Install Requirements
Bash
pip install -r requirements.txt
3. Setup Environment Variables
Create a .env file in the root directory:

Code snippet
DISCORD_TOKEN=your_discord_bot_token
PLEX_URL=http://your_plex_ip:32400
PLEX_TOKEN=your_plex_token

Commands
/music - Opens the Live Control Panel. Use this to start a session.
/play [query] - Fast-search for a specific track and play it immediately.
/queue - Shows the next 15 tracks in the current queue.
/clear - Stops the music and clears the current queue.

User Interface Flow
Search: Select the Search button and type an artist name.

Select Mode: Choose Discography (Shuffle all), Artist Radio (A mix of the artist and similar music), or Pick an Album.

Control: Use the live tile to adjust volume or skip tracks without additional commands.
