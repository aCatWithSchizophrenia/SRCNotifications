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

# ---------------------- CONFIG ----------------------
load_dotenv()

DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
if not DISCORD_BOT_TOKEN:
    raise ValueError("DISCORD_BOT_TOKEN not set in .env!")

BASE_URL = "https://www.speedrun.com/api/v1"
SEEN_RUNS_FILE = "seen_runs.json"
CONFIG_FILE = "config.json"
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

# ---------------------- LOAD STATE ----------------------
if os.path.exists(SEEN_RUNS_FILE):
    with open(SEEN_RUNS_FILE, "r") as f:
        try:
            seen_runs = set(json.load(f))
            logger.info(f"📁 Loaded {len(seen_runs)} seen runs from history")
        except Exception as e:
            logger.warning(f"❌ Failed to load seen runs: {e}, starting fresh")
            seen_runs = set()
else:
    logger.info("📁 No seen runs history found, starting fresh")
    seen_runs = set()

# Default configuration template
DEFAULT_SERVER_CONFIG = {
    "channel_id": None,
    "role_id": None,
    "games": ["Destiny 2", "Destiny 2 Misc", "Destiny 2 Portal", "Destiny 2 Lost Sectors", "Destiny 2 Story"],
    "interval": 60,
    "enabled": True
}

def load_config():
    """Load configuration with server-specific settings"""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)
            logger.info("📋 Config loaded successfully")
            return config
    logger.info("📋 No config found, using defaults")
    return {"servers": {}}

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
    logger.info("💾 Config saved")

def get_server_config(guild_id):
    """Get server-specific configuration"""
    guild_id = str(guild_id)
    if guild_id not in bot_config["servers"]:
        # Initialize with default config for this server
        bot_config["servers"][guild_id] = DEFAULT_SERVER_CONFIG.copy()
        save_config(bot_config)
        logger.info(f"🆕 Initialized config for server {guild_id}")
    return bot_config["servers"][guild_id]

def update_server_config(guild_id, updates):
    """Update server-specific configuration"""
    guild_id = str(guild_id)
    if guild_id not in bot_config["servers"]:
        bot_config["servers"][guild_id] = DEFAULT_SERVER_CONFIG.copy()
    
    bot_config["servers"][guild_id].update(updates)
    save_config(bot_config)
    logger.info(f"⚙️ Updated config for server {guild_id}")

bot_config = load_config()

# Global variables for current context (will be set per server during operations)
current_server_config = None
last_announced_runs = []

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
bot = commands.Bot(command_prefix="!", intents=intents)
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

def save_seen_runs():
    try:
        with open(SEEN_RUNS_FILE, "w") as f:
            json.dump(list(seen_runs), f)
        logger.info(f"💾 Saved {len(seen_runs)} seen runs to history")
    except Exception as e:
        logger.error(f"❌ Failed to save seen runs: {e}")

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

# ---------------------- ENHANCED GAME DETECTION ----------------------
async def find_game_id(session, game_name):
    """Enhanced game finding with better search"""
    # Try exact match first
    exact_url = f"{BASE_URL}/games?name={game_name.replace(' ', '%20')}&max=10"
    data = await fetch_json(session, exact_url)

    if not data or "data" not in data:
        return None, None

    # Look for exact match first
    for game in data["data"]:
        if game["names"]["international"].lower() == game_name.lower():
            logger.info(f"🎯 Exact match found: {game_name} -> {game['names']['international']} (ID: {game['id']})")
            return game["id"], game["names"]["international"]

    # Look for partial matches
    for game in data["data"]:
        if game_name.lower() in game["names"]["international"].lower():
            logger.info(f"🔍 Partial match: {game_name} -> {game['names']['international']} (ID: {game['id']})")
            return game["id"], game["names"]["international"]

    # Use first result if no good match found
    if data["data"]:
        game = data["data"][0]
        logger.info(f"📝 Using first result: {game_name} -> {game['names']['international']} (ID: {game['id']})")
        return game["id"], game["names"]["international"]

    return None, None

# ---------------------- NOTIFICATION ----------------------
async def notify_new_run(session, run, game_name, server_config):
    """Notify a specific server about a new run"""
    try:
        run_id = run.get("id")
        if not run_id or run_id in seen_runs:
            return

        # Skip if server has disabled notifications
        if not server_config.get("enabled", True):
            return

        run_url = f"{BASE_URL}/runs/{run_id}?embed=category,level,variables,platform"
        full_run_resp = await fetch_json(session, run_url)
        full_run = full_run_resp["data"] if full_run_resp and "data" in full_run_resp else run

        detailed_category = await get_detailed_category_info(session, full_run)
        runner = await get_player_name(session, full_run.get("players", [{}])[0])
        
        # Format the time properly
        raw_time = full_run.get("times", {}).get("primary_t")
        run_time = format_time(raw_time) if raw_time else "Unknown"
        
        platform_name = await resolve_platform(session, full_run)
        submitted = full_run.get("submitted", "Unknown")
        weblink = full_run.get("weblink", "https://www.speedrun.com")

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

        if full_run.get("players"):
            player = full_run["players"][0]
            if player.get("rel") == "user" and "id" in player:
                user_data = await fetch_json(session, f"{BASE_URL}/users/{player['id']}")
                if user_data and "data" in user_data:
                    assets = user_data["data"].get("assets", {})
                    image_url = assets.get("image", {}).get("uri")
                    if image_url:
                        embed.set_thumbnail(url=image_url)

        channel_id = server_config.get("channel_id")
        if channel_id:
            try:
                channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
                if channel:
                    role_id = server_config.get("role_id")
                    ping_text = f"<@&{role_id}>" if role_id else None
                    await channel.send(content=ping_text, embed=embed)
                    seen_runs.add(run_id)
                    save_seen_runs()
                    logger.info(f"📢 Notified server {channel.guild.name} about new run: {game_name} by {runner}")
                else:
                    logger.warning(f"❌ Channel {channel_id} not found for server")
            except discord.Forbidden:
                logger.warning(f"❌ No permission to send messages in channel {channel_id}")
            except Exception as e:
                logger.error(f"❌ Error sending notification to server: {e}")

    except Exception as e:
        logger.error(f"❌ Error notifying run {run.get('id')}: {e}")

# ---------------------- ENHANCED CHECK RUNS ----------------------
async def check_new_runs():
    """Check for new runs and notify all configured servers"""
    logger.info("🔍 Starting run check for all servers...")
    total_new_runs = 0
    servers_notified = 0

    async with aiohttp.ClientSession() as session:
        # Get all unique games from all server configurations
        all_games = set()
        for server_id, server_config in bot_config["servers"].items():
            if server_config.get("enabled", True) and server_config.get("channel_id"):
                all_games.update(server_config.get("games", []))

        if not all_games:
            logger.info("ℹ️ No enabled servers with configured games")
            return

        logger.info(f"🎮 Checking games for all servers: {', '.join(all_games)}")

        for game_name in all_games:
            logger.info(f"🎮 Checking game: {game_name}")

            game_id, game_name_actual = await find_game_id(session, game_name)

            if not game_id:
                logger.warning(f"❌ Could not find game: {game_name}")
                continue

            runs_url = f"{BASE_URL}/runs?game={game_id}&status=new&max=20&orderby=submitted&direction=desc"
            runs_data = await fetch_json(session, runs_url)

            if not runs_data or "data" not in runs_data:
                logger.info(f"ℹ️ No runs data for {game_name_actual}")
                continue

            new_runs = [r for r in runs_data["data"] if r.get("id") not in seen_runs]

            if new_runs:
                logger.info(f"✅ Found {len(new_runs)} new runs for {game_name_actual}")
                total_new_runs += len(new_runs)
                
                # Notify all servers that are monitoring this game
                for run in new_runs:
                    for server_id, server_config in bot_config["servers"].items():
                        if (server_config.get("enabled", True) and 
                            server_config.get("channel_id") and 
                            game_name in server_config.get("games", [])):
                            await notify_new_run(session, run, game_name_actual, server_config)
                            servers_notified += 1
            else:
                logger.info(f"ℹ️ No new runs for {game_name_actual}")

    if total_new_runs > 0:
        logger.info(f"🎉 Check complete! Found {total_new_runs} new runs, notified {servers_notified} server instances")
    else:
        logger.info("🔍 Check complete! No new runs found")

# ---------------------- TASK LOOP ----------------------
@tasks.loop(seconds=60)  # Default interval, will be adjusted per server config
async def monitor_runs():
    await check_new_runs()

# ---------------------- COMPLETE COMMANDS ----------------------
@bot.command()
@commands.has_permissions(administrator=True)
async def setchannel(ctx):
    """Set notification channel for this server"""
    server_config = get_server_config(ctx.guild.id)
    update_server_config(ctx.guild.id, {"channel_id": ctx.channel.id})
    
    logger.info(f"📢 Notification channel set to: {ctx.channel.id} for server {ctx.guild.name}")
    await ctx.send(f"✅ Notifications will now be sent to this channel for server **{ctx.guild.name}**.")

@bot.command()
@commands.has_permissions(administrator=True)
async def setrole(ctx, role: discord.Role):
    """Set ping role for this server"""
    server_config = get_server_config(ctx.guild.id)
    update_server_config(ctx.guild.id, {"role_id": role.id})
    
    logger.info(f"🔔 Ping role set to: {role.name} (ID: {role.id}) for server {ctx.guild.name}")
    await ctx.send(f"✅ Role {role.mention} will now be pinged for new runs in server **{ctx.guild.name}**.")

@bot.command()
@commands.has_permissions(administrator=True)
async def setgames(ctx, *games):
    """Set games to monitor for this server"""
    if not games:
        await ctx.send("❌ Please specify at least one game. Example: `!setgames \"Game 1\" \"Game 2\"`")
        return
    
    server_config = get_server_config(ctx.guild.id)
    update_server_config(ctx.guild.id, {"games": list(games)})
    
    logger.info(f"🎮 Games updated for server {ctx.guild.name}: {', '.join(games)}")
    await ctx.send(f"✅ Now monitoring games for server **{ctx.guild.name}**: {', '.join(games)}")

@bot.command()
@commands.has_permissions(administrator=True)
async def interval(ctx, seconds: int):
    """Set check interval for this server"""
    if seconds < 30:
        await ctx.send("❌ Interval must be at least 30 seconds to avoid rate limiting.")
        return
        
    server_config = get_server_config(ctx.guild.id)
    update_server_config(ctx.guild.id, {"interval": seconds})
    
    logger.info(f"⏰ Check interval set to: {seconds} seconds for server {ctx.guild.name}")
    await ctx.send(f"✅ Monitoring interval set to {seconds} seconds for server **{ctx.guild.name}**.")

@bot.command()
async def config(ctx):
    """Show current configuration for this server"""
    server_config = get_server_config(ctx.guild.id)
    
    embed = discord.Embed(
        title=f"🤖 Bot Configuration - {ctx.guild.name}",
        color=discord.Color.blue()
    )
    embed.add_field(name="🎮 Games", value="\n".join(server_config.get("games", [])) or "None", inline=False)
    embed.add_field(name="👀 Global Seen Runs", value=str(len(seen_runs)), inline=True)
    embed.add_field(name="⏰ Interval", value=f"{server_config.get('interval', 60)} seconds", inline=True)
    embed.add_field(name="📢 Channel", value=f"<#{server_config.get('channel_id')}>" if server_config.get("channel_id") else "Not set", inline=True)
    embed.add_field(name="🔔 Role", value=f"<@&{server_config.get('role_id')}>" if server_config.get("role_id") else "Not set", inline=True)
    embed.add_field(name="✅ Enabled", value="Yes" if server_config.get("enabled", True) else "No", inline=True)
    
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def enable(ctx):
    """Enable notifications for this server"""
    server_config = get_server_config(ctx.guild.id)
    update_server_config(ctx.guild.id, {"enabled": True})
    
    logger.info(f"✅ Notifications enabled for server {ctx.guild.name}")
    await ctx.send(f"✅ Notifications enabled for server **{ctx.guild.name}**.")

@bot.command()
@commands.has_permissions(administrator=True)
async def disable(ctx):
    """Disable notifications for this server"""
    server_config = get_server_config(ctx.guild.id)
    update_server_config(ctx.guild.id, {"enabled": False})
    
    logger.info(f"❌ Notifications disabled for server {ctx.guild.name}")
    await ctx.send(f"❌ Notifications disabled for server **{ctx.guild.name}**.")

@bot.command()
@commands.has_permissions(administrator=True)
async def resetserver(ctx):
    """Reset configuration for this server to defaults"""
    update_server_config(ctx.guild.id, DEFAULT_SERVER_CONFIG.copy())
    
    logger.info(f"🔄 Config reset to defaults for server {ctx.guild.name}")
    await ctx.send(f"✅ Configuration reset to defaults for server **{ctx.guild.name}**.")

@bot.command()
@commands.has_permissions(administrator=True)
async def serverinfo(ctx):
    """Show information about all servers using this bot"""
    embed = discord.Embed(title="🌐 Bot Server Information", color=discord.Color.green())
    
    total_servers = len(bot_config["servers"])
    enabled_servers = sum(1 for config in bot_config["servers"].values() if config.get("enabled", True))
    
    embed.add_field(name="📊 Total Servers", value=str(total_servers), inline=True)
    embed.add_field(name="✅ Enabled Servers", value=str(enabled_servers), inline=True)
    embed.add_field(name="🤖 Bot User", value=bot.user.name, inline=True)
    
    # Show configured servers
    configured_servers = []
    for server_id, config in bot_config["servers"].items():
        guild = bot.get_guild(int(server_id))
        if guild:
            status = "✅" if config.get("enabled", True) else "❌"
            games_count = len(config.get("games", []))
            configured_servers.append(f"{status} {guild.name} ({games_count} games)")
    
    if configured_servers:
        embed.add_field(name="🖥️ Configured Servers", value="\n".join(configured_servers[:10]), inline=False)
        if len(configured_servers) > 10:
            embed.add_field(name="ℹ️ Note", value=f"... and {len(configured_servers) - 10} more servers", inline=False)
    
    await ctx.send(embed=embed)

@bot.command()
async def test(ctx):
    """Test if bot is working for this server"""
    server_config = get_server_config(ctx.guild.id)
    status = "✅ Enabled" if server_config.get("enabled", True) else "❌ Disabled"
    channel_set = "✅ Set" if server_config.get("channel_id") else "❌ Not set"
    
    embed = discord.Embed(title="🧪 Bot Test", color=discord.Color.green())
    embed.add_field(name="Server Status", value=status, inline=True)
    embed.add_field(name="Channel", value=channel_set, inline=True)
    embed.add_field(name="Games Monitored", value=str(len(server_config.get("games", []))), inline=True)
    embed.add_field(name="Bot Response", value="✅ Working!", inline=False)
    
    await ctx.send(embed=embed)

# ... (keep the existing invite, help, checknow commands unchanged, but update help text)

@bot.command()
async def help(ctx):
    """Show help message with all commands"""
    embed = discord.Embed(
        title="📖 Speedrun Bot Help - Multi-Server",
        description="This bot supports multiple servers with independent configurations!",
        color=discord.Color.blue()
    )

    commands_list = [
        ("!help", "Show this help message"),
        ("!test", "Test bot functionality for this server"),
        ("!config", "Show current settings for this server"),
        ("!serverinfo", "Show info about all servers using this bot"),
        ("!setchannel", "Set notification channel (Server-specific)"),
        ("!setrole @role", "Set ping role (Server-specific)"),
        ("!setgames game1 game2", "Set games to monitor (Server-specific)"),
        ("!interval seconds", "Set check interval (Server-specific)"),
        ("!enable", "Enable notifications for this server"),
        ("!disable", "Disable notifications for this server"),
        ("!resetserver", "Reset this server's configuration to defaults"),
        ("!checknow", "Manually check for new runs immediately"),
        ("!invite", "Get bot invite link for other servers"),
        ("!resetseen", "Clear global seen runs history (Admin only)")
    ]

    for cmd, desc in commands_list:
        embed.add_field(name=cmd, value=desc, inline=False)

    embed.set_footer(text="Server-specific commands affect only the current server")
    await ctx.send(embed=embed)

# ---------------------- ON READY ----------------------
@bot.event
async def on_ready():
    logger.info("=" * 50)
    logger.info(f"🤖 Bot is ready! Logged in as {bot.user}")
    logger.info(f"🆔 Bot ID: {bot.user.id}")
    logger.info(f"📊 Connected to {len(bot.guilds)} guild(s)")
    
    # Log server configurations
    enabled_servers = sum(1 for config in bot_config["servers"].values() if config.get("enabled", True))
    configured_servers = sum(1 for config in bot_config["servers"].values() if config.get("channel_id"))
    
    logger.info(f"🌐 {enabled_servers} servers enabled, {configured_servers} servers configured")
    
    for guild in bot.guilds:
        server_config = get_server_config(guild.id)
        status = "✅" if server_config.get("enabled", True) else "❌"
        logger.info(f"🖥️ {status} {guild.name} (ID: {guild.id})")
    
    logger.info("=" * 50)

    monitor_runs.start()

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