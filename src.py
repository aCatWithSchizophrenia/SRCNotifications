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


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)
            logger.info("📋 Config loaded successfully")
            return config
    logger.info("📋 No config found, using defaults")
    return {
        "channel_id": None,
        "role_id": None,
        "games": ["Destiny 2", "Destiny 2 Misc", "Destiny 2 Portal", "Destiny 2 Lost Sectors", "Destiny 2 Story"],
        "interval": 60
    }


def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
    logger.info("💾 Config saved")


bot_config = load_config()
CHANNEL_ID = bot_config.get("channel_id")
ROLE_ID = bot_config.get("role_id")
ALLOWED_GAME_NAMES = bot_config.get("games", [
    "Destiny 2",
    "Destiny 2 Misc",
    "Destiny 2 Portal",
    "Destiny 2 Lost Sectors",
    "Destiny 2 Story"
])
INTERVAL_SECONDS = bot_config.get("interval", 60)

last_announced_runs = []

# Log loaded configuration
logger.info(f"🎮 Monitoring {len(ALLOWED_GAME_NAMES)} games: {', '.join(ALLOWED_GAME_NAMES)}")
logger.info(f"⏰ Check interval: {INTERVAL_SECONDS} seconds")
logger.info(f"📢 Notification channel: {CHANNEL_ID or 'Not set'}")
logger.info(f"🔔 Ping role: {ROLE_ID or 'Not set'}")

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
async def notify_new_run(session, run, game_name):
    try:
        run_id = run.get("id")
        if not run_id or run_id in seen_runs:
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

        channel = bot.get_channel(CHANNEL_ID) or await bot.fetch_channel(CHANNEL_ID)
        if channel:
            ping_text = f"<@&{ROLE_ID}>" if ROLE_ID else None
            await channel.send(content=ping_text, embed=embed)
            seen_runs.add(run_id)
            save_seen_runs()
            last_announced_runs.append(run_id)
            if len(last_announced_runs) > 20:
                last_announced_runs.pop(0)
            logger.info(f"📢 Notified about new run: {game_name} by {runner}")
        else:
            logger.error("❌ Channel not found!")

    except Exception as e:
        logger.error(f"❌ Error notifying run {run.get('id')}: {e}")


# ---------------------- ENHANCED CHECK RUNS ----------------------
async def check_new_runs():
    logger.info("🔍 Starting run check...")
    total_new_runs = 0

    async with aiohttp.ClientSession() as session:
        for game_name in ALLOWED_GAME_NAMES:
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
                for run in new_runs:
                    await notify_new_run(session, run, game_name_actual)
            else:
                logger.info(f"ℹ️ No new runs for {game_name_actual}")

    if total_new_runs > 0:
        logger.info(f"🎉 Check complete! Found {total_new_runs} total new runs")
    else:
        logger.info("🔍 Check complete! No new runs found")


# ---------------------- TASK LOOP ----------------------
@tasks.loop(seconds=INTERVAL_SECONDS)
async def monitor_runs():
    await check_new_runs()


# ---------------------- COMPLETE COMMANDS ----------------------
@bot.command()
@commands.has_permissions(administrator=True)
async def setchannel(ctx):
    global CHANNEL_ID, bot_config
    CHANNEL_ID = ctx.channel.id
    bot_config["channel_id"] = CHANNEL_ID
    save_config(bot_config)
    logger.info(f"📢 Notification channel set to: {CHANNEL_ID}")
    await ctx.send(f"✅ Notifications will now be sent to this channel.")


@bot.command()
@commands.has_permissions(administrator=True)
async def setrole(ctx, role: discord.Role):
    global ROLE_ID, bot_config
    ROLE_ID = role.id
    bot_config["role_id"] = ROLE_ID
    save_config(bot_config)
    logger.info(f"🔔 Ping role set to: {role.name} (ID: {ROLE_ID})")
    await ctx.send(f"✅ Role {role.mention} will now be pinged for new runs.")


@bot.command()
@commands.has_permissions(administrator=True)
async def setgames(ctx, *games):
    global ALLOWED_GAME_NAMES, bot_config
    ALLOWED_GAME_NAMES = list(games)
    bot_config["games"] = ALLOWED_GAME_NAMES
    save_config(bot_config)
    logger.info(f"🎮 Games updated to: {', '.join(ALLOWED_GAME_NAMES)}")
    await ctx.send(f"✅ Monitoring games: {', '.join(ALLOWED_GAME_NAMES)}")


@bot.command()
@commands.has_permissions(administrator=True)
async def interval(ctx, seconds: int):
    global INTERVAL_SECONDS, bot_config, monitor_runs
    INTERVAL_SECONDS = seconds
    bot_config["interval"] = seconds
    save_config(bot_config)
    monitor_runs.change_interval(seconds=seconds)
    logger.info(f"⏰ Check interval set to: {seconds} seconds")
    await ctx.send(f"✅ Monitoring interval set to {seconds} seconds.")


@bot.command()
async def config(ctx):
    embed = discord.Embed(title="🤖 Bot Configuration", color=discord.Color.blue())
    embed.add_field(name="🎮 Games", value="\n".join(ALLOWED_GAME_NAMES) or "None", inline=False)
    embed.add_field(name="👀 Seen Runs", value=str(len(seen_runs)), inline=True)
    embed.add_field(name="⏰ Interval", value=f"{INTERVAL_SECONDS} seconds", inline=True)
    embed.add_field(name="📢 Channel", value=f"<#{CHANNEL_ID}>" if CHANNEL_ID else "Not set", inline=True)
    embed.add_field(name="🔔 Role", value=f"<@&{ROLE_ID}>" if ROLE_ID else "Not set", inline=True)
    await ctx.send(embed=embed)


@bot.command()
async def last(ctx, n: int = 5):
    if not last_announced_runs:
        await ctx.send("No runs have been announced yet.")
        return
    recent = list(last_announced_runs)[-n:]
    embed = discord.Embed(title=f"📋 Last {n} Announced Runs", color=discord.Color.green())
    for i, run_id in enumerate(recent, 1):
        embed.add_field(name=f"Run {i}", value=run_id, inline=False)
    await ctx.send(embed=embed)


@bot.command()
@commands.has_permissions(administrator=True)
async def resetseen(ctx):
    global seen_runs
    seen_runs = set()
    save_seen_runs()
    logger.info("🧹 Seen runs history cleared")
    await ctx.send("✅ Seen runs history cleared.")


@bot.command()
async def test(ctx):
    await ctx.send("✅ Bot is working!")


@bot.command()
@commands.has_permissions(administrator=True)
async def clearconfig(ctx):
    """Clear the config.json file"""
    try:
        default_config = {
            "channel_id": None,
            "role_id": None,
            "games": ["Destiny 2", "Destiny 2 Misc", "Destiny 2 Portal", "Destiny 2 Lost Sectors", "Destiny 2 Story"],
            "interval": 60
        }

        with open(CONFIG_FILE, 'w') as f:
            json.dump(default_config, f, indent=4)

        global bot_config, CHANNEL_ID, ROLE_ID, ALLOWED_GAME_NAMES, INTERVAL_SECONDS
        bot_config = default_config
        CHANNEL_ID = None
        ROLE_ID = None
        ALLOWED_GAME_NAMES = default_config["games"]
        INTERVAL_SECONDS = 60
        monitor_runs.change_interval(seconds=INTERVAL_SECONDS)

        logger.info("🔄 Config reset to defaults")
        await ctx.send("✅ Config cleared! All settings reset to defaults.")

    except Exception as e:
        logger.error(f"❌ Error clearing config: {e}")
        await ctx.send(f"❌ Error clearing config: {e}")


@bot.command()
@commands.has_permissions(administrator=True)
async def debuggames(ctx):
    """Debug command to see game matching"""
    async with aiohttp.ClientSession() as session:
        embed = discord.Embed(title="🔍 Game Debug Info", color=discord.Color.blue())

        for game_name in ALLOWED_GAME_NAMES:
            game_id, found_name = await find_game_id(session, game_name)
            status = f"✅ {found_name} (ID: {game_id})" if game_id else "❌ Not found"
            embed.add_field(name=game_name, value=status, inline=False)

        await ctx.send(embed=embed)


@bot.command()
async def help(ctx):
    """Show help message with all commands"""
    embed = discord.Embed(
        title="📖 Speedrun Bot Help",
        description="Here are all available commands:",
        color=discord.Color.blue()
    )

    commands_list = [
        ("!help", "Show this help message"),
        ("!test", "Check if the bot is working"),
        ("!config", "Show current bot settings"),
        ("!last [n]", "Show the last n announced runs (default: 5)"),
        ("!checknow", "Manually check for new runs immediately"),
        ("!debuggames", "Debug game matching (Admin only)"),
        ("!setchannel", "Set notification channel (Admin only)"),
        ("!setrole @role", "Set role to ping for new runs (Admin only)"),
        ("!setgames game1 game2", "Set games to monitor (Admin only)"),
        ("!interval seconds", "Set check interval in seconds (Admin only)"),
        ("!resetseen", "Clear seen runs history (Admin only)"),
        ("!clearconfig", "Reset all settings to defaults (Admin only)")
    ]

    for cmd, desc in commands_list:
        embed.add_field(name=cmd, value=desc, inline=False)

    embed.set_footer(text="Admin commands require administrator permissions")
    await ctx.send(embed=embed)


@bot.command()
@commands.has_permissions(administrator=True)
async def checknow(ctx):
    """Manually check for new runs with detailed results"""
    try:
        embed = discord.Embed(
            title="🔍 Manual Run Check",
            description="Checking for new speedruns...",
            color=discord.Color.blue()
        )
        embed.add_field(name="Status", value="In progress...", inline=True)
        embed.add_field(name="Games", value="\n".join(ALLOWED_GAME_NAMES), inline=True)

        message = await ctx.send(embed=embed)
        logger.info("🔄 Manual check initiated by user")

        results = []
        new_runs_found = 0

        async with aiohttp.ClientSession() as session:
            for game_name in ALLOWED_GAME_NAMES:
                game_id, game_name_actual = await find_game_id(session, game_name)

                if not game_id:
                    results.append(f"❌ {game_name}: Game not found")
                    continue

                runs_url = f"{BASE_URL}/runs?game={game_id}&status=new&max=10&orderby=submitted&direction=desc"
                runs_data = await fetch_json(session, runs_url)

                if not runs_data or "data" not in runs_data:
                    results.append(f"⚠️ {game_name_actual}: No runs data")
                    continue

                new_runs = [r for r in runs_data["data"] if r.get("id") not in seen_runs]
                runs_count = len(new_runs)
                new_runs_found += runs_count

                if runs_count > 0:
                    results.append(f"✅ {game_name_actual}: {runs_count} new run(s)")
                    for run in new_runs:
                        await notify_new_run(session, run, game_name_actual)
                else:
                    results.append(f"ℹ️ {game_name_actual}: No new runs")

        embed.title = "✅ Manual Check Complete"
        embed.color = discord.Color.green() if new_runs_found > 0 else discord.Color.orange()
        embed.clear_fields()

        embed.add_field(name="Results", value="\n".join(results) or "No results", inline=False)
        embed.add_field(name="Total New Runs", value=str(new_runs_found), inline=True)
        embed.add_field(name="Checked Games", value=str(len(ALLOWED_GAME_NAMES)), inline=True)

        await message.edit(embed=embed)
        logger.info(f"✅ Manual check completed: {new_runs_found} new runs found")

    except Exception as e:
        logger.error(f"❌ Manual check failed: {e}")
        error_embed = discord.Embed(
            title="❌ Manual Check Failed",
            description=f"Error: {str(e)}",
            color=discord.Color.red()
        )
        await ctx.send(embed=error_embed)


# ---------------------- ON READY ----------------------
@bot.event
async def on_ready():
    logger.info("=" * 50)
    logger.info(f"🤖 Bot is ready! Logged in as {bot.user}")
    logger.info(f"🆔 Bot ID: {bot.user.id}")
    logger.info(f"📊 Connected to {len(bot.guilds)} guild(s)")
    logger.info("=" * 50)

    monitor_runs.start()
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("🤖 Speedrun monitor bot is now online!")
        logger.info("📢 Online announcement sent")
        


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