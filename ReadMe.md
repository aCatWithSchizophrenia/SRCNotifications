#  __**SRC notifier**__ 

Small discord bot that sends messages if new Destiny 2 runs are submitted to be verified. Might add more features. Only works with a single server for multiple servers use [Multi-Server](https://github.com/aCatWithSchizophrenia/SRCNotifications-Multiserver)

# Requirements

## Python Packages
```txt
aiohttp>=3.8.0
discord.py>=2.3.0
python-dotenv>=1.0.0
```

## Environment Variables
```env
DISCORD_BOT_TOKEN=your_discord_bot_token_here
```

## Permissions Requirements
**Discord Bot Scopes:**
- `bot`
- `applications.commands`

**Bot Permissions:**
- `Send Messages`
- `Embed Links`
- `Read Message History`
- `View Channel`

**User Permissions:**
- Administrator role required for admin commands

## System Requirements
- Python 3.8 or higher
- Internet connection (for speedrun.com API access)
- Write permissions (for JSON file storage)

Guide:
  1. Create a Bot on discord via the [Discord Developer Portal](https://discord.com/developers/applications)
  2. go to Bot Settings and copy the Token:
   
     <img width="363" height="571" alt="image" src="https://github.com/user-attachments/assets/dc0e1906-0866-47d3-b6a0-56b3abdad0eb" />
     <img width="1060" height="190" alt="image" src="https://github.com/user-attachments/assets/eb9c60fe-e317-4297-aa1f-c7aeaeb40214" /> 
     (i changed mine after taking this image)

  3. Paste your Token in the .env
<img width="754" height="39" alt="image" src="https://github.com/user-attachments/assets/960dbde5-2cb7-4531-aabc-c4b22fda288b" />
  4. run the code
  5. go to the channel you want the bot to post in and use !setchannel

Command Explanations

🛠️ Admin Commands

    !setchannel: Bind notifications to the current channel.

    !setrole @Role: Set a specific role to be pinged for new runs.

    !setgames game1 game2 ...: Update the list of games the bot monitors. Separate each game name with a space.

    !interval [seconds]: Change the frequency of the bot's check for new runs. The time is set in seconds.

    !resetseen: Clear the bot's history of seen runs, forcing it to check all recent runs on the next scan.

    !clearconfig: Reset the bot's configuration (channel_id, role_id, games, and interval) to its default settings.

📊 Info Commands

    !help: Display a complete list of all available bot commands.

    !test: A simple command to check if the bot is responsive and working.

    !config: Show the bot's current settings, including games, channel, role, and check interval.

    !last [n]: Show the weblinks for the last n runs the bot has announced (defaults to 5 if no number is specified).

🔍 Debugging & Manual Commands

    !checknow: Manually trigger an immediate check for new runs, bypassing the scheduled interval. The bot will report its findings directly in the channel.

    !debuggames: A diagnostic tool that shows the bot's internal matching status for each game you've configured. This is useful for troubleshooting if a game isn't being detected correctly.

Made by Lasse Pfannenschmidt

For questions or issues, feel free to add me on Discord: acatwithschizophrenia
