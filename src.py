import asyncio
import aiohttp
import logging
import json
import os
import discord
from discord.ext import tasks, commands
from dotenv import load_dotenv

# ---------------------- CONFIG ----------------------
load_dotenv()

DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
if not DISCORD_BOT_TOKEN:
    raise ValueError("DISCORD_BOT_TOKEN not set in .env!")

BASE_URL = "https://www.speedrun.com/api/v1"
SEEN_RUNS_FILE = "seen_runs.json"
CONFIG_FILE = "config.json"

# ---------------------- LOGGING ----------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ---------------------- LOAD STATE ----------------------
if os.path.exists(SEEN_RUNS_FILE):
    with open(SEEN_RUNS_FILE, "r") as f:
        try:
            seen_runs = set(json.load(f))
        except Exception:
            seen_runs = set()
else:
    seen_runs = set()

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {"channel_id": None, "role_id": None, "games": ["Destiny 2"], "interval": 1}

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f)
    logger.info("✅ Config saved")

bot_config = load_config()
CHANNEL_ID = bot_config.get("channel_id")
ROLE_ID = bot_config.get("role_id")
ALLOWED_GAME_NAMES = bot_config.get("games", ["Destiny 2"])
INTERVAL_MINUTES = bot_config.get("interval", 1)

last_announced_runs = []  # store run_ids for !last

# ---------------------- BOT ----------------------
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# remove default help so we can override
bot.remove_command("help")

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
        logger.error(f"Error getting detailed category info: {e}")
        return "Unknown Category"

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
        run_time = full_run.get("times", {}).get("primary_t", "Unknown")
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
        embed.add_field(name="⏱️ Time", value=str(run_time), inline=True)
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
@tasks.loop(minutes=INTERVAL_MINUTES)
async def monitor_runs():
    await check_new_runs()

# ---------------------- COMMANDS ----------------------
@bot.command()
@commands.has_permissions(administrator=True)
async def setchannel(ctx):
    global CHANNEL_ID, bot_config
    CHANNEL_ID = ctx.channel.id
    bot_config["channel_id"] = CHANNEL_ID
    save_config(bot_config)
    await ctx.send(f"✅ Notifications will now be sent to this channel.")

@bot.command()
@commands.has_permissions(administrator=True)
async def setrole(ctx, role: discord.Role):
    global ROLE_ID, bot_config
    ROLE_ID = role.id
    bot_config["role_id"] = ROLE_ID
    save_config(bot_config)
    await ctx.send(f"✅ Role {role.mention} will now be pinged for new runs.")

@bot.command()
@commands.has_permissions(administrator=True)
async def setgames(ctx, *games):
    global ALLOWED_GAME_NAMES, bot_config
    ALLOWED_GAME_NAMES = list(games)
    bot_config["games"] = ALLOWED_GAME_NAMES
    save_config(bot_config)
    await ctx.send(f"✅ Monitoring games: {', '.join(ALLOWED_GAME_NAMES)}")

@bot.command()
@commands.has_permissions(administrator=True)
async def interval(ctx, minutes: int):
    global INTERVAL_MINUTES, bot_config, monitor_runs
    INTERVAL_MINUTES = minutes
    bot_config["interval"] = minutes
    save_config(bot_config)
    monitor_runs.change_interval(minutes=minutes)
    await ctx.send(f"✅ Monitoring interval set to {minutes} minute(s).")

@bot.command()
async def stats(ctx):
    await ctx.send(
        f"**Bot Stats:**\n"
        f"🔎 Monitoring: {len(ALLOWED_GAME_NAMES)} games\n"
        f"👀 Seen runs: {len(seen_runs)}\n"
        f"⏱️ Interval: {INTERVAL_MINUTES} min\n"
        f"📢 Notifications channel: {CHANNEL_ID}\n"
        f"🔔 Ping role: {ROLE_ID if ROLE_ID else 'None'}"
    )

@bot.command()
async def last(ctx, n: int = 5):
    if not last_announced_runs:
        await ctx.send("No runs have been announced yet.")
        return
    recent = list(last_announced_runs)[-n:]
    await ctx.send("**Last announced runs:**\n" + "\n".join(recent))

@bot.command()
@commands.has_permissions(administrator=True)
async def resetseen(ctx):
    global seen_runs
    seen_runs = set()
    save_seen_runs()
    await ctx.send("✅ Seen runs history cleared.")

@bot.command()
async def test(ctx):
    await ctx.send("✅ Bot is working!")

@bot.command()
async def help(ctx):
    embed = discord.Embed(
        title="📖 Speedrun Bot Help",
        description="Here are all available commands:",
        color=discord.Color.blue()
    )
    embed.add_field(name="!help", value="Show this help message.", inline=False)
    embed.add_field(name="!test", value="Check if the bot is working.", inline=False)
    embed.add_field(name="!stats", value="Show monitoring stats (games, runs, interval, etc.).", inline=False)
    embed.add_field(name="!last [n]", value="Show the last *n* announced runs (default 5).", inline=False)
    embed.add_field(name="!resetseen", value="Clear the seen runs history. (Admin only)", inline=False)
    embed.add_field(name="!setchannel", value="Bind the bot to the current channel. (Admin only)", inline=False)
    embed.add_field(name="!setrole @Role", value="Set a role to ping when a new run is found. (Admin only)", inline=False)
    embed.add_field(name="!setgames game1 game2 ...", value="Update the list of monitored games. (Admin only)", inline=False)
    embed.add_field(name="!interval <minutes>", value="Change monitoring interval in minutes. (Admin only)", inline=False)

    await ctx.send(embed=embed)

# ---------------------- ON READY ----------------------
@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user}")
    monitor_runs.start()
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("🤖 Speedrun monitor bot is now online!")

# ---------------------- RUN BOT ----------------------
if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)
