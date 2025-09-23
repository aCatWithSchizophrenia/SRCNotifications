import asyncio
import aiohttp
import logging
import json
import os
import discord
from discord.ext import tasks, commands
from dotenv import load_dotenv

# ---------------------- CONFIG ----------------------
load_dotenv()  # Load .env

DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
if not DISCORD_BOT_TOKEN:
    raise ValueError("DISCORD_BOT_TOKEN not set in .env!")

BASE_URL = "https://www.speedrun.com/api/v1"
ALLOWED_GAME_NAMES = [
    "Destiny 2",
    "Destiny 2 Portal",
    "Destiny 2 misc",
    "Destiny 2 Lost Sectors",
    "Destiny 2 Story"
]
SEEN_RUNS_FILE = "seen_runs.json"
CONFIG_FILE = "config.json"

# Load seen runs
if os.path.exists(SEEN_RUNS_FILE):
    with open(SEEN_RUNS_FILE, "r") as f:
        try:
            seen_runs = set(json.load(f))
        except Exception:
            seen_runs = set()
else:
    seen_runs = set()

# Load bot config
def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {"channel_id": None}

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f)
    logger.info("✅ Config saved")

bot_config = load_config()
CHANNEL_ID = bot_config.get("channel_id")  # this will be updated by !setchannel

# ---------------------- LOGGING ----------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ---------------------- BOT ----------------------
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------------- HELPERS ----------------------
async def fetch_json(session, url):
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                logger.warning(f"Request failed: {url} (HTTP {resp.status})")
                return None
            return await resp.json()
    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
        return None

def save_seen_runs():
    try:
        with open(SEEN_RUNS_FILE, "w") as f:
            json.dump(list(seen_runs), f)
        logger.info(f"Saved {len(seen_runs)} seen runs.")
    except Exception as e:
        logger.error(f"Failed to save seen runs: {e}")

# --- Player name resolver ---
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

# --- Platform resolver ---
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

# --- Category resolver ---
async def get_detailed_category_info(session, run):
    try:
        category_name = "Unknown Category"
        level_name = None
        variable_details = []

        # CATEGORY
        category_data = None
        if isinstance(run.get("category"), dict) and "data" in run["category"]:
            category_data = run["category"]["data"]
        elif isinstance(run.get("category"), str):
            cat_json = await fetch_json(session, f"{BASE_URL}/categories/{run['category']}")
            if cat_json and "data" in cat_json:
                category_data = cat_json["data"]
        if category_data:
            category_name = category_data.get("name", "Unknown Category")

        # LEVEL
        level_data = None
        if isinstance(run.get("level"), dict) and "data" in run["level"]:
            level_data = run["level"]["data"]
        elif isinstance(run.get("level"), str):
            lvl_json = await fetch_json(session, f"{BASE_URL}/levels/{run['level']}")
            if lvl_json and "data" in lvl_json:
                level_data = lvl_json["data"]
        if level_data:
            level_name = level_data.get("name")

        # VARIABLES
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
        logger.error(f"Error getting detailed category info: {e}")
        return "Unknown Category"

# ---------------------- NOTIFICATION ----------------------
async def notify_new_run(session, run, game_name):
    try:
        run_id = run.get("id")
        if not run_id or run_id in seen_runs:
            return

        # Fetch full run details
        run_url = f"{BASE_URL}/runs/{run_id}?embed=category,level,variables,platform"
        full_run_resp = await fetch_json(session, run_url)
        full_run = full_run_resp["data"] if full_run_resp and "data" in full_run_resp else run

        detailed_category = await get_detailed_category_info(session, full_run)
        runner = await get_player_name(session, full_run.get("players", [{}])[0])
        run_time = full_run.get("times", {}).get("primary_t", "Unknown")
        platform_name = await resolve_platform(session, full_run)
        submitted = full_run.get("submitted", "Unknown")

        message = (
            f"🚨 **New {game_name} Speedrun Needs Verification!** 🚨\n\n"
            f"🏃 **Runner:** {runner}\n"
            f"📂 **Category:** {detailed_category}\n"
            f"⏱️ **Time:** {run_time}\n"
            f"💻 **Platform:** {platform_name}\n"
            f"📅 **Submitted:** {submitted}\n"
            f"🔗 **View Run:** {full_run.get('weblink', 'https://www.speedrun.com')}"
        )

        channel = bot.get_channel(CHANNEL_ID) or await bot.fetch_channel(CHANNEL_ID)
        if channel:
            await channel.send(message)
            seen_runs.add(run_id)
            save_seen_runs()
        else:
            logger.error("❌ Channel not found!")

    except Exception as e:
        logger.error(f"Error notifying run {run.get('id')}: {e}")

# ---------------------- CHECK RUNS ----------------------
async def check_new_runs():
    async with aiohttp.ClientSession() as session:
        for game_name in ALLOWED_GAME_NAMES:
            search_url = f"{BASE_URL}/games?name={game_name.replace(' ', '%20')}&max=1"
            data = await fetch_json(session, search_url)
            if not data or "data" not in data or not data["data"]:
                continue
            game_id = data["data"][0]["id"]
            game_name_actual = data["data"][0]["names"]["international"]
            runs_url = f"{BASE_URL}/runs?game={game_id}&status=new&max=10&orderby=submitted&direction=desc"
            runs_data = await fetch_json(session, runs_url)
            if not runs_data or "data" not in runs_data:
                continue
            new_runs = [r for r in runs_data["data"] if r.get("id") not in seen_runs]
            for run in new_runs:
                await notify_new_run(session, run, game_name_actual)

# ---------------------- TASK LOOP ----------------------
@tasks.loop(minutes=1)
async def monitor_runs():
    await check_new_runs()

# ---------------------- COMMANDS ----------------------
@bot.command()
@commands.has_permissions(administrator=True)
async def setchannel(ctx):
    """Bind the bot to the current channel."""
    global CHANNEL_ID, bot_config
    CHANNEL_ID = ctx.channel.id
    bot_config["channel_id"] = CHANNEL_ID
    save_config(bot_config)
    await ctx.send(f"✅ This channel is now set for notifications! (ID: {CHANNEL_ID})")


@bot.command()
async def test(ctx):
    await ctx.send("✅ Bot is working!")

@bot.command()
async def status(ctx):
    await ctx.send(f"**Bot Status:** ✅ Online\n**Monitoring:** {len(ALLOWED_GAME_NAMES)} games\n**Seen runs:** {len(seen_runs)}")

# ---------------------- ON READY ----------------------
@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user}")
    monitor_runs.start()
    # Optionally send startup message
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("🤖 Speedrun monitor bot is now online!")

# ---------------------- RUN BOT ----------------------
if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)
