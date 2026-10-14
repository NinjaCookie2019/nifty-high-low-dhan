# NIFTY 50 High/Low Breakout Alert Bot

A Python script that monitors NIFTY 50 index and sends Telegram alerts when the previous day's high or low is broken.

## Features

- 📊 Fetches previous day's high/low from Dhan API
- 💹 Monitors real-time NIFTY 50 price during market hours
- 🔔 Sends Telegram alerts when high/low is broken
- 🕐 Automatically detects market hours (9:15 AM - 3:30 PM IST)
- 📅 Resets alerts at the start of each trading day
- ♻️ Restores same-day alert state after a restart to avoid duplicate breakout alerts
- 🔐 Saves renewed tokens to a persistent volume without restarting the worker
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

# Legacy variables (no longer required with a persistent volume)
RAILWAY_API_TOKEN=your_railway_api_token
RAILWAY_PROJECT_ID=your_railway_project_id
RAILWAY_ENVIRONMENT_ID=your_railway_environment_id
RAILWAY_SERVICE_ID=your_railway_service_id

# Optional test switch (set true only when testing renewal flow)
FORCE_TOKEN_RENEW_ON_START=false

# Optional: points beyond previous high/low required to re-arm repeat breakout alerts
BREAKOUT_REARM_BUFFER=1
```

### Durable token storage and recovery

Mount a Railway volume at `/data`. The worker uses `RAILWAY_VOLUME_MOUNT_PATH`
for its renewed Dhan token (owner-only file permissions) and daily alert state.
The newer of the cached token and `DHAN_API_TOKEN` is selected at startup, then
validated with Dhan. Each renewed token is saved immediately, before the next
profile request. Renewal no longer updates Railway variables or triggers a deploy.
The legacy `RAILWAY_API_TOKEN` is not needed by the worker.

Authentication failures pause price monitoring, send an operational Telegram
warning, and retry every 60 seconds. An expired token cannot be renewed: generate
a fresh token in Dhan, update `DHAN_API_TOKEN` in Railway, and redeploy. Missing
required configuration exits nonzero. Telegram alert flags are persisted only
after Telegram accepts the message, so rejected deliveries remain retryable.

Run `python verify_delivery.py` in the deployed container to send two clearly
labelled synthetic high/low tests through the production alert function. This does
not modify the live worker's alert state. Run with `--market-data` to additionally
validate Dhan authentication, historical levels and the latest price. Telegram
acceptance confirms delivery to the chat API, not that a person read the message.

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
4. **On token renewal**: Saves the new token on the persistent volume immediately, without redeploying.
5. **On restart**: Reloads successfully delivered same-day alert state; failed sends remain retryable.
6. **On breakout**: Sends Telegram alert, then re-arms after price pulls back through the high/low by `BREAKOUT_REARM_BUFFER` points.
7. **New day**: Resets state and fetches new previous day data.

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
