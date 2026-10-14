"""Explicit operational test; synthetic messages, isolated state, no order APIs."""
import argparse
import sys
import nifty_high_low_alert as bot
from token_manager import DhanTokenManager
from token_storage import load_token


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-data", action="store_true")
    args = parser.parse_args()
    sender = bot.send_telegram_message
    results = []
    def labelled(message):
        ok = sender("🧪 <b>TEST — SYNTHETIC DATA — NOT A MARKET SIGNAL</b>\n\n" + message)
        results.append(ok)
        return ok
    bot.send_telegram_message = labelled
    high = bot.BreakoutState()
    low = bot.BreakoutState()
    bot.check_breakout(25101, 25100, 24900, high)
    bot.check_breakout(24899, 25100, 24900, low)
    delivered = results == [True, True] and high.high_broken and low.low_broken
    print(f"VERIFICATION: synthetic_high_and_low_delivered={delivered}")
    if not args.market_data:
        return 0 if delivered else 1
    bot.token_manager = DhanTokenManager(
        load_token(bot.TOKEN_CACHE_FILE, bot.DHAN_API_TOKEN, bot.DHAN_CLIENT_ID),
        bot.DHAN_CLIENT_ID,
    )
    valid, _ = bot.token_manager.validate_token()
    levels = bot.get_previous_day_high_low() if valid else (None, None)
    price = bot.get_current_ltp() if valid else None
    data_ok = valid and all(levels) and price is not None and price > 0
    print(f"VERIFICATION: dhan_market_data_available={data_ok}")
    return 0 if delivered and data_ok else 1


if __name__ == "__main__":
    sys.exit(main())
