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
    
CHANNEL_ID = int(os.getenv('DISCORD_CHANNEL_ID'))
if not CHANNEL_ID:
    raise ValueError("DISCORD_CHANNEL_ID not set in .env!")


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

# Load seen runs
if os.path.exists(SEEN_RUNS_FILE):
    with open(SEEN_RUNS_FILE, "r") as f:
        try:
            seen_runs = set(json.load(f))
        except Exception:
            seen_runs = set()
else:
    seen_runs = set()

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
        logger.debug(f"[API] Fetching: {url}")
        async with session.get(url) as resp:
            logger.info(f"[API] Response status: {resp.status} for {url}")
            if resp.status != 200:
                logger.warning(f"Request failed: {url} (HTTP {resp.status})")
                return None
            data = await resp.json()
            return data
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


async def resolve_platform(session, run):
    """Return a human-friendly platform name if possible."""
    # 1) check embed: run.get("platform") -> { "data": { ... } }
    platform_field = run.get("platform")
    if isinstance(platform_field, dict) and "data" in platform_field:
        return platform_field["data"].get("name", "Unknown")

    # 2) check system.platform (may be id or friendly string)
    system = run.get("system", {}) or {}
    sys_platform = system.get("platform")
    if not sys_platform:
        return "Unknown"

    # if sys_platform looks like an id (very likely from list results), try to fetch name
    if isinstance(sys_platform, str) and len(sys_platform) >= 6:
        url = f"{BASE_URL}/platforms/{sys_platform}"
        pdata = await fetch_json(session, url)
        if pdata and "data" in pdata:
            return pdata["data"].get("name", str(sys_platform))

    # fallback: return whatever is in system.platform
    return str(sys_platform)


async def get_detailed_category_info(session, run):
    """
    Resolve category name, optional level name, and variable choices.
    Works with:
      - embedded objects (run["category"]["data"])
      - or fallback to fetching category/level/variables endpoints
    """
    try:
        category_name = "Unknown Category"
        level_name = None
        variable_details = []

        # --- CATEGORY ---
        category_data = None
        if isinstance(run.get("category"), dict) and "data" in run["category"]:
            category_data = run["category"]["data"]
            logger.debug("[DEBUG] Category embedded in run.")
        elif isinstance(run.get("category"), str):
            cat_id = run["category"]
            logger.debug(f"[DEBUG] Category is ID ({cat_id}), fetching category endpoint.")
            cat_json = await fetch_json(session, f"{BASE_URL}/categories/{cat_id}")
            if cat_json and "data" in cat_json:
                category_data = cat_json["data"]

        if category_data:
            category_name = category_data.get("name", "Unknown Category")

        # --- LEVEL ---
        level_data = None
        if isinstance(run.get("level"), dict) and "data" in run["level"]:
            level_data = run["level"]["data"]
            logger.debug("[DEBUG] Level embedded in run.")
        elif isinstance(run.get("level"), str):
            lvl_id = run["level"]
            logger.debug(f"[DEBUG] Level is ID ({lvl_id}), fetching level endpoint.")
            lvl_json = await fetch_json(session, f"{BASE_URL}/levels/{lvl_id}")
            if lvl_json and "data" in lvl_json:
                level_data = lvl_json["data"]

        if level_data:
            level_name = level_data.get("name")

        # --- VARIABLES ---
        # Try embedded variables first (single-run embed)
        variables_data = None
        if isinstance(run.get("variables"), dict) and "data" in run["variables"]:
            variables_data = run["variables"]["data"]
            logger.debug("[DEBUG] Variables embedded in run.")
        else:
            # If not embedded, try to fetch variables from category endpoint (category must be known)
            cat_id_for_vars = None
            if category_data and category_data.get("id"):
                cat_id_for_vars = category_data.get("id")
            elif isinstance(run.get("category"), str):
                cat_id_for_vars = run.get("category")
            if cat_id_for_vars:
                vars_url = f"{BASE_URL}/categories/{cat_id_for_vars}/variables"
                vars_json = await fetch_json(session, vars_url)
                if vars_json and "data" in vars_json:
                    variables_data = vars_json["data"]
                    logger.debug("[DEBUG] Fetched variables from category endpoint.")

        # variables_data may be a list or dict keyed by id — normalize to list
        variables_list = []
        if variables_data:
            if isinstance(variables_data, list):
                variables_list = variables_data
            elif isinstance(variables_data, dict):
                # If the API returned an object keyed by id, collect values
                variables_list = list(variables_data.values())

        # Build mapping for run values (var_id -> choice_id)
        run_values = run.get("values", {}) or {}

        for var in variables_list:
            var_id = var.get("id")
            if not var_id:
                continue
            if var_id not in run_values:
                continue  # this run didn't set this variable
            value_id = run_values[var_id]
            var_name = var.get("name", "Unknown")

            # choices are typically at var["values"]["values"] as a dict
            choices = var.get("values", {}).get("values", {}) or {}
            value_name = None

            if isinstance(choices, dict) and value_id in choices:
                val_obj = choices[value_id]
                if isinstance(val_obj, dict):
                    value_name = val_obj.get("label") or val_obj.get("name") or str(value_id)
                else:
                    value_name = str(val_obj)
            else:
                # fallback: try to iterate choices to find a matching id
                if isinstance(choices, dict):
                    for k, v in choices.items():
                        if k == value_id:
                            if isinstance(v, dict):
                                value_name = v.get("label") or v.get("name") or str(value_id)
                            else:
                                value_name = str(v)
                            break

            if value_name is None:
                value_name = str(value_id)

            variable_details.append(f"{var_name}: {value_name}")
            logger.debug(f"[DEBUG] Variable {var_name} = {value_name}")

        # Build final category string
        result = category_name
        if level_name:
            result += f" - {level_name}"
        if variable_details:
            result += " | " + " | ".join(variable_details)

        logger.info(f"[DEBUG] Final category string: {result}")
        return result

    except Exception as e:
        logger.error(f"Error in get_detailed_category_info: {e}")
        return "Unknown Category"


async def get_player_name(session, player):
    """Handles both guest and registered users safely."""
    if not player:
        return "Unknown"
    if player.get("rel") == "guest":
        return player.get("name", "Unknown")
    elif player.get("rel") == "user" and "id" in player:
        url = f"{BASE_URL}/users/{player['id']}"
        data = await fetch_json(session, url)
        if data and "data" in data:
            return data["data"]["names"].get("international", data["data"].get("name", player["id"]))
        return player["id"]
    # fallback: sometimes player comes as a simple dict with 'name'
    return player.get("name") or str(player)


# ---------------------- CORE LOGIC ----------------------
async def notify_new_run(session, run, game_name):
    try:
        run_id = run.get("id")
        if not run_id:
            logger.warning("Run has no id, skipping.")
            return

        # Skip if already seen
        if run_id in seen_runs:
            logger.info(f"[DEBUG] Run {run_id} already seen, skipping")
            return

        logger.info(f"[TASK] Processing NEW run {run_id} for game '{game_name}'")

        # Skip runs not in allowed list (safety)
        if not any(name.lower() in game_name.lower() for name in ALLOWED_GAME_NAMES):
            logger.info(f"[DEBUG] Skipping run {run_id} from game '{game_name}' (not in allowed list)")
            return

        # Fetch full run details (guarantees embedded objects if available)
        run_url = f"{BASE_URL}/runs/{run_id}?embed=category,level,variables,platform"
        full_run_resp = await fetch_json(session, run_url)
        if not full_run_resp or "data" not in full_run_resp:
            logger.error(f"❌ Could not fetch full run details for {run_id}")
            # Still try to work with the slim run object as fallback
            full_run = run
        else:
            full_run = full_run_resp["data"]

        logger.debug(f"[DEBUG] Full run data (short): {json.dumps({k: full_run.get(k) for k in ['id','category','level','values','platform','system']}, default=str, indent=2)}")

        # Resolve category/level/variables
        detailed_category = await get_detailed_category_info(session, full_run)

        # Runner
        runner = "Unknown"
        players = full_run.get("players") or []
        if players:
            # players[0] usually holds the main runner; API shapes can vary
            runner = await get_player_name(session, players[0])

        # Time formatting
        times = full_run.get("times", {}) or {}
        primary_time = times.get("primary_t")
        if primary_time is not None:
            try:
                primary_time = float(primary_time)
            except Exception:
                primary_time = None

        if primary_time:
            if primary_time < 60:
                run_time = f"{primary_time:.3f}s"
            elif primary_time < 3600:
                minutes = int(primary_time // 60)
                seconds = primary_time % 60
                run_time = f"{minutes}:{seconds:06.3f}"
            else:
                hours = int(primary_time // 3600)
                minutes = int((primary_time % 3600) // 60)
                seconds = primary_time % 60
                run_time = f"{hours}:{minutes:02d}:{seconds:06.3f}"
        else:
            run_time = "Unknown"

        # Platform resolution
        platform_name = await resolve_platform(session, full_run)

        # Submitted date
        submitted = full_run.get("submitted") or full_run.get("date") or "Unknown"
        if submitted != "Unknown":
            try:
                from datetime import datetime
                # ISO -> datetime
                submitted_dt = datetime.fromisoformat(submitted.replace('Z', '+00:00'))
                submitted = submitted_dt.strftime("%Y-%m-%d %H:%M UTC")
            except Exception:
                # leave as-is
                pass

        message = (
            f"🚨 **New {game_name} Speedrun Needs Verification!** 🚨\n\n"
            f"🏃 **Runner:** {runner}\n"
            f"📂 **Category:** {detailed_category}\n"
            f"⏱️ **Time:** {run_time}\n"
            f"💻 **Platform:** {platform_name}\n"
            f"📅 **Submitted:** {submitted}\n"
            f"🔗 **View Run:** {full_run.get('weblink', 'https://www.speedrun.com')}"
        )

        try:
            channel = bot.get_channel(CHANNEL_ID)
            if not channel:
                channel = await bot.fetch_channel(CHANNEL_ID)

            if channel:
                await channel.send(message)
                logger.info(f"✅ SUCCESS: Posted new run {run_id} for {game_name}")
                seen_runs.add(run_id)
                save_seen_runs()
            else:
                logger.error("❌ Channel not found!")

        except Exception as e:
            logger.error(f"❌ Error sending message: {e}")

    except Exception as e:
        logger.error(f"Error notifying run {run.get('id') if run else 'unknown'}: {e}")


async def check_new_runs():
    logger.info("[TASK] Starting to check for new runs...")
    async with aiohttp.ClientSession() as session:
        for game_name in ALLOWED_GAME_NAMES:
            logger.info(f"[TASK] Checking game: {game_name}")

            # Search for the game
            search_url = f"{BASE_URL}/games?name={game_name.replace(' ', '%20')}&max=1"
            data = await fetch_json(session, search_url)

            if not data:
                logger.warning(f"No data returned for {game_name}")
                continue

            if "data" not in data or not data["data"]:
                logger.warning(f"No game found for: {game_name}")
                continue

            game_id = data["data"][0]["id"]
            game_name_actual = data["data"][0]["names"]["international"]
            logger.info(f"[TASK] Found game: {game_name_actual} (ID: {game_id})")

            # Get new runs (list endpoint) - we will fetch full run details later
            runs_url = f"{BASE_URL}/runs?game={game_id}&status=new&max=10&orderby=submitted&direction=desc"
            runs_data = await fetch_json(session, runs_url)

            if not runs_data:
                logger.warning(f"No runs data returned for {game_name}")
                continue

            if "data" not in runs_data:
                logger.warning(f"No 'data' key in runs response for {game_name}")
                continue

            runs = runs_data["data"]
            logger.info(f"[TASK] Found {len(runs)} total runs for {game_name}")

            # Filter only new runs we haven't seen
            new_runs = [r for r in runs if r.get("id") not in seen_runs]
            logger.info(f"[TASK] {len(new_runs)} new runs to process")

            if not new_runs:
                logger.info(f"[TASK] No new runs found for {game_name}")
                continue

            for i, run in enumerate(new_runs):
                run_id = run.get("id", "unknown")
                logger.info(f"[TASK] Processing new run {i+1}/{len(new_runs)}: {run_id}")
                await notify_new_run(session, run, game_name_actual)

    logger.info("[TASK] Finished checking for new runs")


# ---------------------- TASK LOOP ----------------------
@tasks.loop(minutes=1)
async def monitor_runs():
    try:
        logger.info("=== Starting monitoring cycle ===")
        await check_new_runs()
        logger.info("=== Finished monitoring cycle ===")
    except Exception as e:
        logger.error(f"Error in monitor_runs: {e}")


@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    logger.info(f"Connected to {len(bot.guilds)} guild(s)")

    # Verify channel access
    try:
        channel = bot.get_channel(CHANNEL_ID)
        if not channel:
            channel = await bot.fetch_channel(CHANNEL_ID)

        if channel:
            logger.info(f"✅ Channel found: #{channel.name} in {channel.guild.name}")

            # Check permissions
            perms = channel.permissions_for(channel.guild.me)
            if not perms.send_messages:
                logger.error("❌ Bot cannot send messages in this channel!")
            else:
                logger.info("✅ Bot has send message permission!")
        else:
            logger.error("❌ Channel not found!")

    except Exception as e:
        logger.error(f"❌ Channel verification failed: {e}")

    # Send a startup message
    try:
        channel = bot.get_channel(CHANNEL_ID)
        if channel:
            await channel.send("🤖 Speedrun monitor bot is now online and monitoring for new runs!")
            logger.info("✅ Startup message sent")
    except Exception as e:
        logger.error(f"❌ Failed to send startup message: {e}")

    monitor_runs.start()


# Add a test command to verify basic functionality
@bot.command()
async def test(ctx):
    """Test command to check if bot can send messages"""
    await ctx.send("✅ Bot is working! Test message received.")
    logger.info("Test command executed successfully")


@bot.command()
async def status(ctx):
    """Check bot status and seen runs"""
    status_msg = f"**Bot Status:** ✅ Online\n**Monitoring:** {len(ALLOWED_GAME_NAMES)} games\n**Seen runs:** {len(seen_runs)}"
    await ctx.send(status_msg)


@bot.command()
async def debug_run(ctx, run_id: str):
    """Debug a specific run by ID"""
    async with aiohttp.ClientSession() as session:
        run_url = f"{BASE_URL}/runs/{run_id}?embed=category,level,variables,platform"
        run_data = await fetch_json(session, run_url)

        if run_data and "data" in run_data:
            run = run_data["data"]
            detailed_info = await get_detailed_category_info(session, run)
            raw = json.dumps(run, indent=2, default=str)
            # Limit size when sending to discord: send first 1900 chars then mention it's truncated
            truncated = raw if len(raw) <= 1900 else raw[:1900] + "\n...truncated..."
            await ctx.send(f"Debug info for run {run_id}:\n```json\n{truncated}\n```\nCategory: {detailed_info}")
        else:
            await ctx.send("Run not found or error fetching data")


# ---------------------- RUN BOT ----------------------
if __name__ == "__main__":
    try:
        bot.run(DISCORD_BOT_TOKEN)
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
