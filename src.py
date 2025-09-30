import asyncio
import aiohttp
import logging
import json
import os
import discord
from discord.ext import tasks, commands
from dotenv import load_dotenv
from datetime import datetime
import sys
import sqlite3
from typing import List, Dict, Optional, Tuple

# ---------------------- CONFIG ----------------------
load_dotenv()

DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
if not DISCORD_BOT_TOKEN:
    raise ValueError("DISCORD_BOT_TOKEN not set in .env!")

BASE_URL = "https://www.speedrun.com/api/v1"
DATABASE_FILE = "speedrun_bot.db"
LOG_FILE = f"logs/bot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

# Create logs directory if it doesn't exist
os.makedirs('logs', exist_ok=True)

# ---------------------- ENHANCED LOGGING ----------------------
# Create logger
logger = logging.getLogger('src_bot')
logger.setLevel(logging.INFO)

# Create formatters
console_format = logging.Formatter(
    "%(asctime)s [%(levelname)-8s] %(message)s",
    datefmt="%H:%M:%S"
)
file_format = logging.Formatter(
    "%(asctime)s [%(levelname)-8s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# Console handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(console_format)

# File handler with session-based filename
file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(file_format)

# Add handlers
logger.addHandler(console_handler)
logger.addHandler(file_handler)

# Also configure discord.py logging
discord_logger = logging.getLogger('discord')
discord_logger.setLevel(logging.WARNING)
discord_logger.addHandler(console_handler)
discord_logger.addHandler(file_handler)

# Session start log
logger.info("=" * 60)
logger.info(f"🚀 SRC Bot Session Started - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
logger.info("=" * 60)

# ---------------------- DATABASE MANAGER ----------------------
class DatabaseManager:
    def __init__(self, db_file: str):
        self.db_file = db_file
        self.init_database()
    
    def get_connection(self) -> sqlite3.Connection:
        """Get a database connection with row factory"""
        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        return conn
    
    def init_database(self):
        """Initialize database tables"""
        with self.get_connection() as conn:
            # Servers table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS servers (
                    guild_id TEXT PRIMARY KEY,
                    guild_name TEXT NOT NULL,
                    channel_id TEXT,
                    role_id TEXT,
                    interval INTEGER DEFAULT 60,
                    enabled BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Games table (global game cache)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS games (
                    game_id TEXT PRIMARY KEY,
                    game_name TEXT NOT NULL,
                    abbreviation TEXT,
                    release_year INTEGER,
                    players INTEGER,
                    links TEXT,
                    verified BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Server games junction table (which games each server monitors)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS server_games (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    game_name TEXT NOT NULL,
                    game_id TEXT,
                    custom_name TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (guild_id) REFERENCES servers (guild_id),
                    FOREIGN KEY (game_id) REFERENCES games (game_id),
                    UNIQUE(guild_id, game_name)
                )
            ''')
            
            # Seen runs table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS seen_runs (
                    run_id TEXT PRIMARY KEY,
                    game_id TEXT NOT NULL,
                    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Game search cache table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS game_search_cache (
                    search_term TEXT PRIMARY KEY,
                    matches TEXT,  -- JSON array of matches
                    searched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
        logger.info("💾 Database initialized successfully")
    
    # Server management methods
    def get_server(self, guild_id: str) -> Optional[sqlite3.Row]:
        """Get server configuration"""
        with self.get_connection() as conn:
            result = conn.execute(
                'SELECT * FROM servers WHERE guild_id = ?', 
                (str(guild_id),)
            ).fetchone()
            return result
    
    def create_server(self, guild_id: str, guild_name: str) -> bool:
        """Create a new server entry"""
        try:
            with self.get_connection() as conn:
                conn.execute(
                    '''INSERT OR REPLACE INTO servers (guild_id, guild_name, updated_at) 
                       VALUES (?, ?, CURRENT_TIMESTAMP)''',
                    (str(guild_id), guild_name)
                )
                conn.commit()
            logger.info(f"🆕 Created server record: {guild_name} ({guild_id})")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to create server: {e}")
            return False
    
    def update_server(self, guild_id: str, **updates) -> bool:
        """Update server configuration"""
        try:
            with self.get_connection() as conn:
                set_clause = ", ".join([f"{key} = ?" for key in updates.keys()])
                values = list(updates.values())
                values.append(str(guild_id))
                
                conn.execute(
                    f'''UPDATE servers SET {set_clause}, updated_at = CURRENT_TIMESTAMP 
                        WHERE guild_id = ?''',
                    values
                )
                conn.commit()
            logger.info(f"⚙️ Updated server {guild_id}: {updates}")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to update server: {e}")
            return False
    
    # Game management methods
    def get_game(self, game_id: str) -> Optional[sqlite3.Row]:
        """Get game by ID"""
        with self.get_connection() as conn:
            return conn.execute(
                'SELECT * FROM games WHERE game_id = ?', 
                (game_id,)
            ).fetchone()
    
    def add_game(self, game_id: str, game_name: str, **kwargs) -> bool:
        """Add or update a game"""
        try:
            with self.get_connection() as conn:
                conn.execute(
                    '''INSERT OR REPLACE INTO games 
                       (game_id, game_name, abbreviation, release_year, players, links, verified) 
                       VALUES (?, ?, ?, ?, ?, ?, ?)''',
                    (game_id, game_name, kwargs.get('abbreviation'), kwargs.get('release_year'),
                     kwargs.get('players'), json.dumps(kwargs.get('links', [])), True)
                )
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"❌ Failed to add game: {e}")
            return False
    
    def search_games_cache(self, search_term: str) -> Optional[List[Dict]]:
        """Get cached game search results"""
        with self.get_connection() as conn:
            result = conn.execute(
                'SELECT matches FROM game_search_cache WHERE search_term = ?',
                (search_term.lower(),)
            ).fetchone()
            if result:
                return json.loads(result['matches'])
            return None
    
    def cache_game_search(self, search_term: str, matches: List[Dict]):
        """Cache game search results"""
        try:
            with self.get_connection() as conn:
                conn.execute(
                    '''INSERT OR REPLACE INTO game_search_cache 
                       (search_term, matches, searched_at) VALUES (?, ?, CURRENT_TIMESTAMP)''',
                    (search_term.lower(), json.dumps(matches))
                )
                conn.commit()
        except Exception as e:
            logger.error(f"❌ Failed to cache search: {e}")
    
    # Server-game relationship methods
    def get_server_games(self, guild_id: str) -> List[sqlite3.Row]:
        """Get all games monitored by a server"""
        with self.get_connection() as conn:
            return conn.execute(
                '''SELECT sg.*, g.game_name as verified_name 
                   FROM server_games sg 
                   LEFT JOIN games g ON sg.game_id = g.game_id 
                   WHERE sg.guild_id = ? 
                   ORDER BY sg.created_at''',
                (str(guild_id),)
            ).fetchall()
    
    def add_server_game(self, guild_id: str, game_name: str, game_id: str = None, custom_name: str = None) -> bool:
        """Add a game to a server's monitoring list"""
        try:
            with self.get_connection() as conn:
                conn.execute(
                    '''INSERT OR REPLACE INTO server_games 
                       (guild_id, game_name, game_id, custom_name) 
                       VALUES (?, ?, ?, ?)''',
                    (str(guild_id), game_name, game_id, custom_name)
                )
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"❌ Failed to add server game: {e}")
            return False
    
    def remove_server_game(self, guild_id: str, game_name: str) -> bool:
        """Remove a game from a server's monitoring list"""
        try:
            with self.get_connection() as conn:
                conn.execute(
                    'DELETE FROM server_games WHERE guild_id = ? AND game_name = ?',
                    (str(guild_id), game_name)
                )
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"❌ Failed to remove server game: {e}")
            return False
    
    # Seen runs management
    def add_seen_run(self, run_id: str, game_id: str) -> bool:
        """Mark a run as seen"""
        try:
            with self.get_connection() as conn:
                conn.execute(
                    'INSERT OR IGNORE INTO seen_runs (run_id, game_id) VALUES (?, ?)',
                    (run_id, game_id)
                )
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"❌ Failed to add seen run: {e}")
            return False
    
    def is_run_seen(self, run_id: str) -> bool:
        """Check if a run has been seen"""
        with self.get_connection() as conn:
            result = conn.execute(
                'SELECT 1 FROM seen_runs WHERE run_id = ?', 
                (run_id,)
            ).fetchone()
            return result is not None
    
    def get_all_monitored_games(self) -> List[Tuple[str, str]]:
        """Get all unique games being monitored by any server"""
        with self.get_connection() as conn:
            results = conn.execute('''
                SELECT DISTINCT sg.game_name, sg.game_id, g.game_name as verified_name
                FROM server_games sg
                LEFT JOIN games g ON sg.game_id = g.game_id
                WHERE sg.guild_id IN (SELECT guild_id FROM servers WHERE enabled = TRUE)
            ''').fetchall()
            
            games = []
            for row in results:
                game_name = row['verified_name'] or row['game_name']
                game_id = row['game_id']
                if game_id:  # Only include games with verified IDs
                    games.append((game_id, game_name))
            return games
    
    def get_servers_monitoring_game(self, game_id: str) -> List[sqlite3.Row]:
        """Get all servers that are monitoring a specific game"""
        with self.get_connection() as conn:
            return conn.execute('''
                SELECT s.*, sg.game_name, sg.custom_name
                FROM servers s
                JOIN server_games sg ON s.guild_id = sg.guild_id
                WHERE sg.game_id = ? AND s.enabled = TRUE AND s.channel_id IS NOT NULL
            ''', (game_id,)).fetchall()
    
    def get_server_stats(self) -> Dict:
        """Get statistics about servers and games"""
        with self.get_connection() as conn:
            stats = {}
            
            # Server stats
            server_stats = conn.execute('''
                SELECT 
                    COUNT(*) as total_servers,
                    SUM(CASE WHEN enabled = TRUE THEN 1 ELSE 0 END) as enabled_servers,
                    SUM(CASE WHEN channel_id IS NOT NULL THEN 1 ELSE 0 END) as configured_servers
                FROM servers
            ''').fetchone()
            
            stats.update(dict(server_stats))
            
            # Game stats
            game_stats = conn.execute('''
                SELECT 
                    COUNT(DISTINCT game_id) as unique_games,
                    COUNT(*) as total_monitoring_relationships
                FROM server_games 
                WHERE game_id IS NOT NULL
            ''').fetchone()
            
            stats.update(dict(game_stats))
            
            return stats

# Initialize database
db = DatabaseManager(DATABASE_FILE)

# ---------------------- TIME FORMATTING ----------------------
def format_time(seconds):
    """Convert seconds to human-readable time format (MM:SS.ms)"""
    if seconds is None or seconds == "Unknown":
        return "Unknown"
    
    try:
        seconds = float(seconds)
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        
        if hours > 0:
            return f"{hours:01d}:{minutes:02d}:{secs:06.3f}"
        elif minutes > 0:
            return f"{minutes:01d}:{secs:06.3f}"
        else:
            return f"{secs:.3f}s"
    except (ValueError, TypeError):
        return "Unknown"

# ---------------------- BOT ----------------------
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.message_content = True
intents.reactions = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Remove default help command to use our custom one
bot.remove_command("help")

# ---------------------- ENHANCED HELPERS ----------------------
async def fetch_json(session, url):
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                logger.warning(f"🌐 HTTP {resp.status} for {url}")
                return None
            data = await resp.json()
            logger.debug(f"🌐 Successfully fetched {url}")
            return data
    except asyncio.TimeoutError:
        logger.warning(f"⏰ Timeout fetching {url}")
        return None
    except Exception as e:
        logger.error(f"❌ Error fetching {url}: {e}")
        return None

async def get_player_name(session, player):
    if not player:
        return "Unknown"
    if player.get("rel") == "guest":
        return player.get("name", "Unknown")
    elif player.get("rel") == "user" and "id" in player:
        url = f"{BASE_URL}/users/{player['id']}"
        data = await fetch_json(session, url)
        if data and "data" in data:
            return data["data"]["names"].get("international", player["id"])
        return player["id"]
    return player.get("name", "Unknown")

async def resolve_platform(session, run):
    platform_field = run.get("platform")
    if isinstance(platform_field, dict) and "data" in platform_field:
        return platform_field["data"].get("name", "Unknown")
    system = run.get("system", {}) or {}
    sys_platform = system.get("platform")
    if isinstance(sys_platform, str) and len(sys_platform) >= 6:
        pdata = await fetch_json(session, f"{BASE_URL}/platforms/{sys_platform}")
        if pdata and "data" in pdata:
            return pdata["data"].get("name", str(sys_platform))
    return str(sys_platform) if sys_platform else "Unknown"

async def get_detailed_category_info(session, run):
    try:
        category_name = "Unknown Category"
        level_name = None
        variable_details = []

        category_data = None
        if isinstance(run.get("category"), dict) and "data" in run["category"]:
            category_data = run["category"]["data"]
        elif isinstance(run.get("category"), str):
            cat_json = await fetch_json(session, f"{BASE_URL}/categories/{run['category']}")
            if cat_json and "data" in cat_json:
                category_data = cat_json["data"]
        if category_data:
            category_name = category_data.get("name", "Unknown Category")

        level_data = None
        if isinstance(run.get("level"), dict) and "data" in run["level"]:
            level_data = run["level"]["data"]
        elif isinstance(run.get("level"), str):
            lvl_json = await fetch_json(session, f"{BASE_URL}/levels/{run['level']}")
            if lvl_json and "data" in lvl_json:
                level_data = lvl_json["data"]
        if level_data:
            level_name = level_data.get("name")

        variables_data = None
        if isinstance(run.get("variables"), dict) and "data" in run["variables"]:
            variables_data = run["variables"]["data"]
        elif category_data and category_data.get("id"):
            vars_json = await fetch_json(session, f"{BASE_URL}/categories/{category_data['id']}/variables")
            if vars_json and "data" in vars_json:
                variables_data = vars_json["data"]

        variables_list = list(variables_data.values()) if isinstance(variables_data, dict) else variables_data or []
        run_values = run.get("values") or {}
        for var in variables_list:
            var_id = var.get("id")
            if not var_id or var_id not in run_values:
                continue
            value_id = run_values[var_id]
            var_name = var.get("name", "Unknown")
            choices = var.get("values", {}).get("values", {})
            value_name = str(value_id)
            if isinstance(choices, dict) and value_id in choices:
                value_name = choices[value_id].get("label") or str(value_id)
            variable_details.append(f"{var_name}: {value_name}")

        result = category_name
        if level_name:
            result += f" - {level_name}"
        if variable_details:
            result += " | " + " | ".join(variable_details)
        return result
    except Exception as e:
        logger.error(f"❌ Error getting category info: {e}")
        return "Unknown Category"

# ---------------------- GAME VERIFICATION SYSTEM ----------------------
class GameVerificationSystem:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
    
    async def search_games(self, session, game_name: str, limit: int = 5) -> List[Dict]:
        """Search for games on speedrun.com"""
        # Check cache first
        cached_results = self.db.search_games_cache(game_name)
        if cached_results:
            logger.info(f"🎮 Using cached results for: {game_name}")
            return cached_results[:limit]
        
        # Search API
        search_url = f"{BASE_URL}/games?name={game_name.replace(' ', '%20')}&max=20"
        data = await fetch_json(session, search_url)
        
        if not data or "data" not in data:
            return []
        
        matches = []
        for game in data["data"]:
            match_info = {
                "id": game["id"],
                "name": game["names"]["international"],
                "exact_match": game["names"]["international"].lower() == game_name.lower(),
                "partial_match": game_name.lower() in game["names"]["international"].lower(),
                "abbreviation": game.get("abbreviation", ""),
                "release_year": game.get("released", ""),
                "players": game.get("players", ""),
                "links": game.get("links", [])
            }
            matches.append(match_info)
        
        # Cache results
        self.db.cache_game_search(game_name, matches)
        
        # Sort by match quality
        matches.sort(key=lambda x: (not x["exact_match"], not x["partial_match"]))
        return matches[:limit]
    
    async def verify_and_add_game(self, session, game_name: str, game_id: str) -> bool:
        """Verify a game and add to database"""
        # Get full game data
        game_url = f"{BASE_URL}/games/{game_id}"
        game_data = await fetch_json(session, game_url)
        
        if not game_data or "data" not in game_data:
            return False
        
        game = game_data["data"]
        success = self.db.add_game(
            game_id=game["id"],
            game_name=game["names"]["international"],
            abbreviation=game.get("abbreviation"),
            release_year=game.get("released"),
            players=game.get("players"),
            links=game.get("links", [])
        )
        
        return success

# Initialize game verification
game_verifier = GameVerificationSystem(db)

# ---------------------- NOTIFICATION SYSTEM ----------------------
async def notify_new_run(session, run, game_id: str, game_name: str):
    """Notify all servers monitoring this game about a new run"""
    try:
        run_id = run.get("id")
        if not run_id or db.is_run_seen(run_id):
            return

        # Get servers monitoring this game
        servers = db.get_servers_monitoring_game(game_id)
        if not servers:
            return

        # Get full run details
        run_url = f"{BASE_URL}/runs/{run_id}?embed=category,level,variables,platform"
        full_run_resp = await fetch_json(session, run_url)
        full_run = full_run_resp["data"] if full_run_resp and "data" in full_run_resp else run

        detailed_category = await get_detailed_category_info(session, full_run)
        runner = await get_player_name(session, full_run.get("players", [{}])[0])
        
        raw_time = full_run.get("times", {}).get("primary_t")
        run_time = format_time(raw_time) if raw_time else "Unknown"
        
        platform_name = await resolve_platform(session, full_run)
        submitted = full_run.get("submitted", "Unknown")
        weblink = full_run.get("weblink", "https://www.speedrun.com")

        # Create embed
        embed = discord.Embed(
            title=f"🚨 New {game_name} Speedrun Needs Verification!",
            url=weblink,
            description=f"A new run for **{game_name}** was submitted and is awaiting verification.",
            color=discord.Color.red()
        )
        embed.add_field(name="🏃 Runner", value=runner, inline=True)
        embed.add_field(name="📂 Category", value=detailed_category, inline=False)
        embed.add_field(name="⏱️ Time", value=run_time, inline=True)
        embed.add_field(name="💻 Platform", value=platform_name, inline=True)
        embed.add_field(name="📅 Submitted", value=str(submitted), inline=True)
        embed.add_field(name="🔗 Link", value=f"[View Run]({weblink})", inline=False)

        videos = full_run.get("videos", {})
        if videos and "links" in videos and videos["links"]:
            video_link = videos["links"][0].get("uri")
            if video_link:
                embed.add_field(name="▶️ Video", value=f"[Watch Here]({video_link})", inline=False)

        # Notify each server
        notified_servers = 0
        for server in servers:
            try:
                channel = bot.get_channel(int(server['channel_id']))
                if channel:
                    role_id = server['role_id']
                    ping_text = f"<@&{role_id}>" if role_id else None
                    await channel.send(content=ping_text, embed=embed)
                    notified_servers += 1
                    logger.info(f"📢 Notified {server['guild_name']} about run {run_id}")
            except Exception as e:
                logger.error(f"❌ Failed to notify server {server['guild_name']}: {e}")

        # Mark as seen if at least one server was notified
        if notified_servers > 0:
            db.add_seen_run(run_id, game_id)
            logger.info(f"✅ Notified {notified_servers} servers about new run for {game_name}")

    except Exception as e:
        logger.error(f"❌ Error notifying run {run.get('id')}: {e}")

# ---------------------- CHECK RUNS ----------------------
async def check_new_runs():
    """Check for new runs across all monitored games"""
    logger.info("🔍 Starting run check...")
    
    # Get all unique games being monitored
    monitored_games = db.get_all_monitored_games()
    if not monitored_games:
        logger.info("ℹ️ No games being monitored by any server")
        return
    
    logger.info(f"🎮 Checking {len(monitored_games)} games across all servers")
    
    async with aiohttp.ClientSession() as session:
        for game_id, game_name in monitored_games:
            logger.info(f"🔎 Checking {game_name} (ID: {game_id})")
            
            runs_url = f"{BASE_URL}/runs?game={game_id}&status=new&max=20&orderby=submitted&direction=desc"
            runs_data = await fetch_json(session, runs_url)
            
            if not runs_data or "data" not in runs_data:
                logger.info(f"ℹ️ No runs data for {game_name}")
                continue
            
            new_runs = [r for r in runs_data["data"] if not db.is_run_seen(r.get("id", ""))]
            
            if new_runs:
                logger.info(f"✅ Found {len(new_runs)} new runs for {game_name}")
                for run in new_runs:
                    await notify_new_run(session, run, game_id, game_name)
            else:
                logger.info(f"ℹ️ No new runs for {game_name}")
    
    logger.info("✅ Run check complete")

# ---------------------- TASK LOOP ----------------------
@tasks.loop(seconds=60)
async def monitor_runs():
    await check_new_runs()

# ---------------------- BOT COMMANDS ----------------------
@bot.event
async def on_guild_join(guild):
    """Automatically create server record when bot joins a guild"""
    db.create_server(str(guild.id), guild.name)
    logger.info(f"🤖 Joined new guild: {guild.name} ({guild.id})")

@bot.event
async def on_guild_remove(guild):
    """Handle bot removal from guild"""
    logger.info(f"🤖 Removed from guild: {guild.name} ({guild.id})")

@bot.command()
@commands.has_permissions(administrator=True)
async def setup(ctx):
    """Initial setup command for the server"""
    # Create server record if it doesn't exist
    server = db.get_server(str(ctx.guild.id))
    if not server:
        db.create_server(str(ctx.guild.id), ctx.guild.name)
    
    embed = discord.Embed(
        title="🤖 Speedrun Bot Setup",
        description="Welcome! Let's get your server configured.",
        color=discord.Color.blue()
    )
    
    embed.add_field(
        name="📋 Setup Steps",
        value="1. Set notification channel: `!setchannel`\n"
              "2. Add games to monitor: `!addgame \"Game Name\"`\n"
              "3. Optional: Set ping role: `!setrole @role`\n"
              "4. Optional: Adjust check interval: `!interval 60`",
        inline=False
    )
    
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def setchannel(ctx):
    """Set notification channel for this server"""
    db.update_server(str(ctx.guild.id), channel_id=str(ctx.channel.id))
    
    embed = discord.Embed(
        title="✅ Channel Set",
        description=f"Notifications will be sent to {ctx.channel.mention}",
        color=discord.Color.green()
    )
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def setrole(ctx, role: discord.Role):
    """Set ping role for this server"""
    db.update_server(str(ctx.guild.id), role_id=str(role.id))
    
    embed = discord.Embed(
        title="✅ Role Set",
        description=f"Ping role set to {role.mention}",
        color=discord.Color.green()
    )
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def interval(ctx, seconds: int):
    """Set check interval for this server"""
    if seconds < 30:
        await ctx.send("❌ Interval must be at least 30 seconds")
        return
    
    db.update_server(str(ctx.guild.id), interval=seconds)
    
    embed = discord.Embed(
        title="✅ Interval Set",
        description=f"Check interval set to {seconds} seconds",
        color=discord.Color.green()
    )
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def addgame(ctx, *game_names):
    """Add one or multiple games to monitor with smart verification"""
    if not game_names:
        embed = discord.Embed(
            title="❌ Missing Game Names",
            description="Please specify one or more games to add.\n\n**Examples:**\n`!addgame \"Celeste\"`\n`!addgame \"Celeste\" \"Hollow Knight\" \"Super Meat Boy\"`",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    # Limit the number of games that can be added at once
    if len(game_names) > 10:
        embed = discord.Embed(
            title="❌ Too Many Games",
            description="Please add no more than 10 games at a time to avoid rate limiting.",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    embed = discord.Embed(
        title="🔍 Searching for Games...",
        description=f"Searching for {len(game_names)} game(s) on speedrun.com...",
        color=discord.Color.blue()
    )
    embed.add_field(name="Games to Add", value="\n".join([f"• {name}" for name in game_names]), inline=False)
    
    message = await ctx.send(embed=embed)
    
    results = {
        'success': [],
        'multiple_matches': [],
        'not_found': [],
        'already_added': []
    }
    
    # Get current games to check for duplicates
    current_games = db.get_server_games(str(ctx.guild.id))
    current_game_names = [game['game_name'].lower() for game in current_games]
    
    async with aiohttp.ClientSession() as session:
        for game_name in game_names:
            # Check if game is already added
            if game_name.lower() in current_game_names:
                results['already_added'].append(game_name)
                continue
            
            matches = await game_verifier.search_games(session, game_name, limit=5)
            
            if not matches:
                results['not_found'].append(game_name)
                continue
            
            # Check for exact match
            exact_matches = [m for m in matches if m['exact_match']]
            if exact_matches:
                # Auto-select exact match
                selected = exact_matches[0]
                success = await game_verifier.verify_and_add_game(session, game_name, selected["id"])
                if success:
                    db.add_server_game(str(ctx.guild.id), game_name, selected["id"])
                    results['success'].append(f"**{game_name}** → {selected['name']}")
                else:
                    results['not_found'].append(game_name)
            elif len(matches) == 1:
                # Single match (not exact)
                selected = matches[0]
                success = await game_verifier.verify_and_add_game(session, game_name, selected["id"])
                if success:
                    db.add_server_game(str(ctx.guild.id), game_name, selected["id"])
                    results['success'].append(f"**{game_name}** → {selected['name']} (close match)")
                else:
                    results['not_found'].append(game_name)
            else:
                # Multiple matches - store for interactive selection
                results['multiple_matches'].append({
                    'search_term': game_name,
                    'matches': matches
                })
    
    # Create results embed
    result_embed = discord.Embed(
        title="✅ Game Addition Results",
        color=discord.Color.green() if results['success'] else discord.Color.orange()
    )
    
    if results['success']:
        result_embed.add_field(
            name="✅ Successfully Added",
            value="\n".join(results['success']),
            inline=False
        )
    
    if results['already_added']:
        result_embed.add_field(
            name="ℹ️ Already Added",
            value="\n".join([f"• {name}" for name in results['already_added']]),
            inline=False
        )
    
    if results['not_found']:
        result_embed.add_field(
            name="❌ Not Found",
            value="\n".join([f"• {name}" for name in results['not_found']]),
            inline=False
        )
    
    if results['multiple_matches']:
        result_embed.add_field(
            name="🔍 Needs Selection",
            value=f"Found multiple matches for {len(results['multiple_matches'])} game(s). Use the reactions below to select the correct ones.",
            inline=False
        )
    
    await message.edit(embed=result_embed)
    
    # Handle multiple matches with interactive selection
    if results['multiple_matches']:
        for i, match_data in enumerate(results['multiple_matches']):
            game_name = match_data['search_term']
            matches = match_data['matches']
            
            selection_embed = discord.Embed(
                title=f"🎮 Multiple Matches Found: {game_name}",
                description="Please react with the number of the correct game:",
                color=discord.Color.blue()
            )
            
            for j, match in enumerate(matches[:5], 1):
                status = "✅ Exact" if match["exact_match"] else "🔍 Partial"
                selection_embed.add_field(
                    name=f"{j}. {match['name']} {status}",
                    value=f"Year: {match.get('release_year', 'N/A')} | Players: {match.get('players', 'N/A')}",
                    inline=False
                )
            
            selection_embed.add_field(
                name="❌ Skip",
                value="React with ❌ to skip this game",
                inline=False
            )
            
            selection_msg = await ctx.send(embed=selection_embed)
            
            numbers = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"][:len(matches)]
            for emoji in numbers + ["❌"]:
                await selection_msg.add_reaction(emoji)
            
            def check(reaction, user):
                return user == ctx.author and str(reaction.emoji) in numbers + ["❌"] and reaction.message.id == selection_msg.id
            
            try:
                reaction, user = await bot.wait_for('reaction_add', timeout=60.0, check=check)
                
                if str(reaction.emoji) == "❌":
                    skip_embed = discord.Embed(
                        description=f"⏭️ Skipped game: **{game_name}**",
                        color=discord.Color.orange()
                    )
                    await selection_msg.edit(embed=skip_embed)
                    continue
                
                index = numbers.index(str(reaction.emoji))
                selected = matches[index]
                
                # Verify and add the selected game
                success = await game_verifier.verify_and_add_game(session, game_name, selected["id"])
                if success:
                    db.add_server_game(str(ctx.guild.id), game_name, selected["id"])
                    
                    success_embed = discord.Embed(
                        title="✅ Game Added",
                        description=f"**{game_name}** → {selected['name']}",
                        color=discord.Color.green()
                    )
                    await selection_msg.edit(embed=success_embed)
                else:
                    error_embed = discord.Embed(
                        title="❌ Error",
                        description=f"Failed to add **{game_name}**",
                        color=discord.Color.red()
                    )
                    await selection_msg.edit(embed=error_embed)
                
            except asyncio.TimeoutError:
                timeout_embed = discord.Embed(
                    description=f"⏰ Selection timed out for: **{game_name}**",
                    color=discord.Color.orange()
                )
                await selection_msg.edit(embed=timeout_embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def addgames(ctx, *game_names):
    """Quickly add multiple games (auto-selects first match)"""
    if not game_names:
        embed = discord.Embed(
            title="❌ Missing Game Names",
            description="Please specify games to add.\n\n**Example:**\n`!addgames \"Celeste\" \"Hollow Knight\" \"Super Meat Boy\"`",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    if len(game_names) > 15:
        embed = discord.Embed(
            title="❌ Too Many Games",
            description="Please add no more than 15 games at a time.",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    embed = discord.Embed(
        title="⚡ Quick-Adding Games...",
        description=f"Quickly adding {len(game_names)} game(s) (using first match)...",
        color=discord.Color.blue()
    )
    
    message = await ctx.send(embed=embed)
    
    results = {
        'added': [],
        'failed': [],
        'duplicates': []
    }
    
    current_games = db.get_server_games(str(ctx.guild.id))
    current_game_names = [game['game_name'].lower() for game in current_games]
    
    async with aiohttp.ClientSession() as session:
        for game_name in game_names:
            if game_name.lower() in current_game_names:
                results['duplicates'].append(game_name)
                continue
            
            matches = await game_verifier.search_games(session, game_name, limit=1)
            
            if matches:
                selected = matches[0]
                success = await game_verifier.verify_and_add_game(session, game_name, selected["id"])
                if success:
                    db.add_server_game(str(ctx.guild.id), game_name, selected["id"])
                    match_type = "exact" if selected['exact_match'] else "close"
                    results['added'].append(f"**{game_name}** → {selected['name']} ({match_type} match)")
                else:
                    results['failed'].append(game_name)
            else:
                results['failed'].append(game_name)
    
    # Create results embed
    result_embed = discord.Embed(
        title="✅ Quick-Add Results",
        color=discord.Color.green() if results['added'] else discord.Color.orange()
    )
    
    if results['added']:
        result_embed.add_field(
            name="✅ Added Games",
            value="\n".join(results['added'][:10]),  # Limit to first 10
            inline=False
        )
        if len(results['added']) > 10:
            result_embed.add_field(
                name="ℹ️ Note",
                value=f"And {len(results['added']) - 10} more games were added.",
                inline=False
            )
    
    if results['duplicates']:
        result_embed.add_field(
            name="ℹ️ Already Added",
            value=", ".join(results['duplicates'][:5]),
            inline=False
        )
    
    if results['failed']:
        result_embed.add_field(
            name="❌ Not Found",
            value=", ".join(results['failed'][:5]),
            inline=False
        )
        if len(results['failed']) > 5:
            result_embed.add_field(
                name="ℹ️ Note",
                value=f"And {len(results['failed']) - 5} more games not found.",
                inline=False
            )
    
    await message.edit(embed=result_embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def removegame(ctx, *, game_name: str):
    """Remove a game from monitoring"""
    success = db.remove_server_game(str(ctx.guild.id), game_name)
    
    if success:
        embed = discord.Embed(
            title="✅ Game Removed",
            description=f"**{game_name}** is no longer being monitored",
            color=discord.Color.green()
        )
    else:
        embed = discord.Embed(
            title="❌ Error",
            description=f"Game '{game_name}' not found in your list",
            color=discord.Color.red()
        )
    
    await ctx.send(embed=embed)

@bot.command()
async def listgames(ctx):
    """List all games being monitored by this server"""
    games = db.get_server_games(str(ctx.guild.id))
    
    embed = discord.Embed(
        title=f"🎮 Monitored Games - {ctx.guild.name}",
        color=discord.Color.blue()
    )
    
    if not games:
        embed.description = "No games being monitored. Use `!addgame` to add games."
    else:
        for game in games:
            status = "✅ Verified" if game['game_id'] else "❓ Unverified"
            display_name = game['verified_name'] or game['game_name']
            embed.add_field(
                name=f"{display_name} {status}",
                value=f"ID: {game['game_id'] or 'Not verified'}",
                inline=False
            )
    
    await ctx.send(embed=embed)

@bot.command()
async def config(ctx):
    """Show current server configuration"""
    server = db.get_server(str(ctx.guild.id))
    games = db.get_server_games(str(ctx.guild.id))
    
    if not server:
        await ctx.send("❌ Server not configured. Use `!setup` to get started.")
        return
    
    embed = discord.Embed(
        title=f"⚙️ Configuration - {ctx.guild.name}",
        color=discord.Color.blue()
    )
    
    embed.add_field(name="Status", value="✅ Enabled" if server['enabled'] else "❌ Disabled", inline=True)
    embed.add_field(name="Interval", value=f"{server['interval']} seconds", inline=True)
    embed.add_field(name="Channel", value=f"<#{server['channel_id']}>" if server['channel_id'] else "Not set", inline=True)
    embed.add_field(name="Role", value=f"<@&{server['role_id']}>" if server['role_id'] else "Not set", inline=True)
    embed.add_field(name="Games", value=str(len(games)), inline=True)
    embed.add_field(name="Server ID", value=server['guild_id'], inline=True)
    
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def enable(ctx):
    """Enable notifications for this server"""
    db.update_server(str(ctx.guild.id), enabled=True)
    await ctx.send("✅ Notifications enabled")

@bot.command()
@commands.has_permissions(administrator=True)
async def disable(ctx):
    """Disable notifications for this server"""
    db.update_server(str(ctx.guild.id), enabled=False)
    await ctx.send("❌ Notifications disabled")

@bot.command()
async def stats(ctx):
    """Show bot statistics"""
    stats = db.get_server_stats()
    
    embed = discord.Embed(title="📊 Bot Statistics", color=discord.Color.green())
    embed.add_field(name="Total Servers", value=stats['total_servers'], inline=True)
    embed.add_field(name="Enabled Servers", value=stats['enabled_servers'], inline=True)
    embed.add_field(name="Configured Servers", value=stats['configured_servers'], inline=True)
    embed.add_field(name="Unique Games", value=stats['unique_games'], inline=True)
    embed.add_field(name="Total Monitoring", value=stats['total_monitoring_relationships'], inline=True)
    
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def checknow(ctx):
    """Manually check for new runs"""
    embed = discord.Embed(description="🔍 Checking for new runs...", color=discord.Color.blue())
    message = await ctx.send(embed=embed)
    
    await check_new_runs()
    
    embed = discord.Embed(description="✅ Manual check complete", color=discord.Color.green())
    await message.edit(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def setgames(ctx, *games):
    """Set multiple games at once (replaces current list)"""
    if not games:
        await ctx.send("❌ Please specify at least one game. Example: `!setgames \"Game 1\" \"Game 2\"`")
        return
    
    # Remove existing games
    existing_games = db.get_server_games(str(ctx.guild.id))
    for game in existing_games:
        db.remove_server_game(str(ctx.guild.id), game['game_name'])
    
    # Add new games
    added_games = []
    failed_games = []
    
    async with aiohttp.ClientSession() as session:
        for game_name in games:
            matches = await game_verifier.search_games(session, game_name, limit=1)
            if matches:
                game_id = matches[0]['id']
                db.add_server_game(str(ctx.guild.id), game_name, game_id)
                added_games.append(f"✅ {game_name} → {matches[0]['name']}")
            else:
                db.add_server_game(str(ctx.guild.id), game_name)
                failed_games.append(f"❓ {game_name} (needs verification)")
    
    embed = discord.Embed(title="🎮 Games Updated", color=discord.Color.green())
    if added_games:
        embed.add_field(name="Added Games", value="\n".join(added_games), inline=False)
    if failed_games:
        embed.add_field(name="Games Needing Verification", value="\n".join(failed_games), inline=False)
    
    await ctx.send(embed=embed)

@bot.command()
async def help(ctx):
    """Show help message with all commands"""
    embed = discord.Embed(
        title="📖 Speedrun Bot Help",
        description="A multi-server bot for monitoring speedrun.com verification queues",
        color=discord.Color.blue()
    )
    
    commands_list = [
        ("!help", "Show this help message"),
        ("!setup", "Initial server setup wizard"),
        ("!config", "Show current server configuration"),
        ("!setchannel", "Set notification channel for this server"),
        ("!setrole @role", "Set role to ping for new runs"),
        ("!interval <seconds>", "Set check interval (min 30 seconds)"),
        ("!addgame \"Game1\" \"Game2\" ...", "Add multiple games with interactive verification"),
        ("!addgames \"Game1\" \"Game2\" ...", "Quick-add multiple games (auto-selects first match)"),
        ("!setgames \"Game1\" \"Game2\" ...", "Replace all games with new list"),
        ("!removegame \"Game Name\"", "Remove a game from monitoring"),
        ("!listgames", "List all games being monitored"),
        ("!enable", "Enable notifications for this server"),
        ("!disable", "Disable notifications for this server"),
        ("!checknow", "Manually check for new runs immediately"),
        ("!stats", "Show bot statistics across all servers"),
        ("!invite", "Get bot invite link"),
        ("!test", "Test bot functionality")
    ]
    
    for cmd, desc in commands_list:
        embed.add_field(name=cmd, value=desc, inline=False)
    
    embed.add_field(
        name="🎮 Game Adding Tips",
        value="• Use quotes for games with spaces: `!addgame \"Hollow Knight\"`\n• For multiple games: `!addgame \"Celeste\" \"Hollow Knight\" \"Super Meat Boy\"`\n• Use `!addgames` for faster bulk adding",
        inline=False
    )
    
    embed.set_footer(text="Admin permissions required for configuration commands")
    await ctx.send(embed=embed)

@bot.command()
async def invite(ctx):
    """Generate bot invite link"""
    permissions = discord.Permissions()
    permissions.read_messages = True
    permissions.send_messages = True
    permissions.embed_links = True
    permissions.attach_files = True
    permissions.read_message_history = True
    permissions.mention_everyone = False
    permissions.use_external_emojis = True
    permissions.add_reactions = True
    
    invite_url = discord.utils.oauth_url(bot.user.id, permissions=permissions)
    
    embed = discord.Embed(
        title="🤖 Bot Invite Link",
        description="Use this link to add the bot to other servers:",
        color=discord.Color.green()
    )
    embed.add_field(
        name="🔗 Invite URL",
        value=f"[Click here to invite]({invite_url})",
        inline=False
    )
    embed.add_field(
        name="📋 Required Permissions",
        value="• Read Messages\n• Send Messages\n• Embed Links\n• Read Message History\n• Add Reactions",
        inline=True
    )
    
    await ctx.send(embed=embed)

@bot.command()
async def test(ctx):
    """Test command to check if bot is responsive"""
    server = db.get_server(str(ctx.guild.id))
    status = "✅ Configured" if server and server['channel_id'] else "❌ Not configured"
    
    embed = discord.Embed(title="🧪 Bot Test", color=discord.Color.green())
    embed.add_field(name="Bot Status", value="✅ Online and responsive", inline=True)
    embed.add_field(name="Server Status", value=status, inline=True)
    embed.add_field(name="Response Time", value="✅ Immediate", inline=True)
    
    if server:
        games = db.get_server_games(str(ctx.guild.id))
        embed.add_field(name="Monitored Games", value=str(len(games)), inline=True)
        embed.add_field(name="Check Interval", value=f"{server['interval']} seconds", inline=True)
    
    await ctx.send(embed=embed)

# ---------------------- ON READY ----------------------
@bot.event
async def on_ready():
    logger.info("=" * 50)
    logger.info(f"🤖 Bot is ready! Logged in as {bot.user}")
    
    # Create server records for all current guilds
    for guild in bot.guilds:
        if not db.get_server(str(guild.id)):
            db.create_server(str(guild.id), guild.name)
    
    stats = db.get_server_stats()
    logger.info(f"🌐 Servers: {stats['total_servers']} total, {stats['enabled_servers']} enabled")
    logger.info(f"🎮 Games: {stats['unique_games']} unique, {stats['total_monitoring_relationships']} relationships")
    logger.info("=" * 50)
    
    monitor_runs.start()

# ---------------------- ERROR HANDLING ----------------------
@bot.event
async def on_command_error(ctx, error):
    """Handle command errors gracefully"""
    if isinstance(error, commands.CommandNotFound):
        # Suggest similar commands or show help
        await ctx.send(f"❌ Command not found. Use `!help` to see all available commands.")
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use this command.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing required argument. Usage: `!{ctx.command.name} {ctx.command.signature}`")
    else:
        logger.error(f"Command error: {error}")
        await ctx.send("❌ An error occurred while executing the command.")

# ---------------------- RUN BOT ----------------------
if __name__ == "__main__":
    try:
        bot.run(DISCORD_BOT_TOKEN)
    except Exception as e:
        logger.critical(f"💥 Bot crashed: {e}")
    finally:
        logger.info("=" * 60)
        logger.info(f"🛑 SRC Bot Session Ended - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 60)