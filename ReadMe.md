#  __**SRC notifier**__ 

Small discord bot that sends messages if new Destiny 2 runs are submitted to be verified. Might add more features. 

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

# Command Explanations

## 🛠️ Admin Commands
**!setchannel** - Bind notifications to current channel  
**!setrole @Role** - Set role to ping for new runs  
**!setgames game1 game2** - Update monitored games list  
**!interval minutes** - Change check frequency (minutes)  
**!resetseen** - Clear seen runs history

## 📊 Info Commands  
**!help** - Show all commands  
**!test** - Check bot responsiveness  
**!stats** - Display monitoring statistics  
**!last [n]** - Show last n announced runs (default: 5)


Made by Lasse Pfannenschmidt

for questions add: __**acatwithschizophrenia**__ on discord 
