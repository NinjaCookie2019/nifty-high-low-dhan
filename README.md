# NIFTY 50 High/Low Breakout Alert Bot

A Python script that monitors NIFTY 50 index and sends Telegram alerts when the previous day's high or low is broken.

## Features

- 📊 Fetches previous day's high/low from Dhan API
- 💹 Monitors real-time NIFTY 50 price during market hours
- 🔔 Sends Telegram alerts when high/low is broken
- 🕐 Automatically detects market hours (9:15 AM - 3:30 PM IST)
- 📅 Resets alerts at the start of each trading day
- ⚡ Configurable check interval

## Prerequisites

1. **Dhan Trading Account** with API access enabled
2. **Telegram Bot** - Create one via [@BotFather](https://t.me/botfather)
3. **Python 3.8+** installed

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Create or update `.env` file with your credentials:

```env
DHAN_API_TOKEN=your_dhan_api_token
DHAN_CLIENT_ID=your_dhan_client_id
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id

# Optional but recommended for automatic token persistence in Railway
RAILWAY_API_TOKEN=your_railway_api_token
RAILWAY_PROJECT_ID=your_railway_project_id
RAILWAY_ENVIRONMENT_ID=your_railway_environment_id
RAILWAY_SERVICE_ID=your_railway_service_id

# Optional test switch (set true only when testing renewal flow)
FORCE_TOKEN_RENEW_ON_START=false
```

### Automatic Railway Token Persistence

If Railway variables above are configured, the bot will automatically:

1. Renew `DHAN_API_TOKEN` before expiry.
2. Update Railway service variable `DHAN_API_TOKEN` using Railway GraphQL API.
3. Continue running even if persistence fails (in-memory token remains active for current process).
4. Send Telegram warnings for renewal/persistence failures and a critical alert if token expires.

This ensures the next scheduled Railway restart starts with the latest token, without manual updates.

Security guidance:
- Use a scoped Railway API token with minimum required permissions.
- Never log or share the full Dhan/Railway token values.
- Rotate Railway API token periodically.

### Test Renewal Immediately (without waiting)

To verify renewal + Railway persistence right now:

1. Set `FORCE_TOKEN_RENEW_ON_START=true` in Railway variables.
2. Deploy/restart the service once.
3. Check Telegram and logs for forced-renew success and Railway update.
4. Set `FORCE_TOKEN_RENEW_ON_START=false` after test (important).

### Getting Telegram Chat ID

1. Create a bot via [@BotFather](https://t.me/botfather) and get the bot token
2. Add the bot to your group/channel
3. Send a message to the group
4. Visit: `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
5. Find the `chat.id` in the response

## Usage

### Run the Script

```bash
python nifty_high_low_alert.py
```

### Running in Background (Linux/Mac)

```bash
nohup python nifty_high_low_alert.py > bot.log 2>&1 &
```

### Using Screen (Recommended)

```bash
screen -S nifty-bot
python nifty_high_low_alert.py
# Press Ctrl+A then D to detach
```

## How It Works

1. **Runs continuously (24x7)**: Process stays alive on Railway.
2. **Outside market hours**: Bot sleeps and only performs periodic token health checks.
3. **During market hours**: Checks NIFTY LTP every 5 seconds (configurable).
4. **On breakout**: Sends Telegram alert (once per day per breakout type).
5. **New day**: Resets state and fetches new previous day data.

## Sample Alerts

### High Breakout Alert
```
🚀 NIFTY 50 - HIGH BREAKOUT!

📈 Previous Day High: 24500.00
💹 Current Price: 24525.50
📊 Breakout by: +25.50 points
🕐 Time: 2024-12-01 10:30:45
```

### Low Breakout Alert
```
🔻 NIFTY 50 - LOW BREAKOUT!

📉 Previous Day Low: 24200.00
💹 Current Price: 24175.00
📊 Breakout by: -25.00 points
🕐 Time: 2024-12-01 14:15:30
```

## Configuration

You can modify the check interval in the script:

```python
# Run with 10-second interval
run_monitor(check_interval=10)
```

## API Rate Limits

Dhan API has the following rate limits:
- 25 requests per second
- 250 requests per minute
- 1000 requests per hour

The default 5-second interval (720 requests/hour) is well within limits.

## License

MIT License
