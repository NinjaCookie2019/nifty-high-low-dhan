import base64
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from datetime import datetime
import requests
import nifty_high_low_alert as bot
from token_manager import DhanTokenManager
from token_storage import load_token, save_token


def fake_jwt(expiry):
    payload = base64.urlsafe_b64encode(json.dumps({'exp': expiry}).encode()).decode().rstrip('=')
    return 'test.' + payload + '.not-a-signature'


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.redirect = contextlib.redirect_stdout(self.output)
        self.redirect.__enter__()

    def tearDown(self):
        self.redirect.__exit__(None, None, None)

    def test_failed_high_delivery_retries_then_deduplicates(self):
        state = bot.BreakoutState()
        with patch.object(bot, 'send_telegram_message', side_effect=[False, True]) as send:
            self.assertFalse(bot.check_breakout(25101, 25100, 24900, state))
            self.assertFalse(state.high_broken)
            self.assertTrue(bot.check_breakout(25101, 25100, 24900, state))
            self.assertFalse(bot.check_breakout(25102, 25100, 24900, state))
            self.assertEqual(send.call_count, 2)

    def test_failed_low_delivery_retries(self):
        state = bot.BreakoutState()
        with patch.object(bot, 'send_telegram_message', side_effect=[False, True]):
            self.assertFalse(bot.check_breakout(24899, 25100, 24900, state))
            self.assertTrue(bot.check_breakout(24899, 25100, 24900, state))
            self.assertTrue(state.low_broken)

    def test_warning_failures_remain_retryable(self):
        for price, flag in [(25099, 'high_warning_sent'), (24901, 'low_warning_sent')]:
            state = bot.BreakoutState()
            with patch.object(bot, 'send_telegram_message', return_value=False):
                self.assertFalse(bot.check_breakout(price, 25100, 24900, state))
                self.assertFalse(getattr(state, flag))

    def test_pullback_rearms_breakout(self):
        state = bot.BreakoutState()
        with patch.object(bot, 'send_telegram_message', return_value=True) as send:
            bot.check_breakout(25101, 25100, 24900, state)
            bot.check_breakout(25098, 25100, 24900, state)
            bot.check_breakout(25101, 25100, 24900, state)
            self.assertEqual(send.call_count, 2)

    def test_restart_preserves_delivered_and_failed_flags(self):
        with tempfile.TemporaryDirectory() as d, patch.object(bot, 'STATE_FILE', Path(d)/'state.json'):
            state = bot.BreakoutState()
            state.reset_for_date(datetime.now(bot.IST).date())
            state.high_broken = True
            bot.save_breakout_state(state)
            loaded = bot.load_breakout_state(datetime.now(bot.IST).date())
            self.assertTrue(loaded.high_broken)
            self.assertFalse(loaded.low_broken)
            with patch.object(bot, 'send_telegram_message', return_value=True) as send:
                bot.check_breakout(25101, 25100, 24900, loaded)
                send.assert_not_called()

    def test_telegram_requires_ok_not_just_http_200(self):
        response = Mock(status_code=200)
        response.json.return_value = {'ok': False, 'error_code': 400}
        with patch.object(bot.requests, 'post', return_value=response):
            self.assertFalse(bot.send_telegram_message('test'))

    def test_telegram_accepts_success(self):
        response = Mock(status_code=200)
        response.json.return_value = {'ok': True, 'result': {'message_id': 123}}
        with patch.object(bot.requests, 'post', return_value=response):
            self.assertTrue(bot.send_telegram_message('test'))
            self.assertIn('message_id=123', self.output.getvalue())

    def test_network_failure_does_not_mark_delivered(self):
        with patch.object(bot.requests, 'post', side_effect=requests.Timeout()):
            self.assertFalse(bot.send_telegram_message('test'))

    def test_auth_outage_retries_and_recovers(self):
        manager = Mock(access_token='dummy')
        with patch.object(bot, 'validate_dhan_token', side_effect=[False, False, True]), patch.object(bot, 'token_manager', manager), patch.object(bot, 'save_token', return_value=(True, None)), patch.object(bot.time, 'sleep') as sleep, patch.object(bot, 'send_telegram_message', return_value=True) as send:
            bot.wait_for_valid_token()
            self.assertEqual(sleep.call_count, 2)
            self.assertEqual(send.call_count, 2)

    def test_missing_configuration_exits_nonzero(self):
        with patch.object(bot, 'DHAN_API_TOKEN', None), self.assertRaises(SystemExit) as raised:
            bot.run_monitor()
        self.assertEqual(raised.exception.code, 1)

    def test_token_cache_private_and_newer_token_wins(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'token.json'
            older, newer = fake_jwt(100), fake_jwt(200)
            self.assertEqual(save_token(p, newer, 'test-client'), (True, None))
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            self.assertEqual(load_token(p, older, 'test-client'), newer)
            self.assertEqual(load_token(p, fake_jwt(300), 'test-client'), fake_jwt(300))
            self.assertEqual(load_token(p, older, 'another-client'), older)

    def test_corrupt_cache_falls_back_to_environment(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'token.json'
            p.write_text('broken')
            self.assertEqual(load_token(p, 'dummy', 'test-client'), 'dummy')

    def test_iso_expiry_is_parsed_without_assuming_24h(self):
        manager = DhanTokenManager('dummy', 'test-client')
        manager._parse_token_expiry('2026-10-14T09:00:00.000')
        self.assertEqual(manager.get_token_expiry().hour, 9)
        self.assertEqual(manager.get_token_expiry().day, 14)

    def test_renewed_token_saved_even_when_profile_request_fails(self):
        saved = Mock(return_value=(True, None))
        manager = DhanTokenManager('dummy', 'test-client', persist_token_func=saved)
        response = Mock(status_code=200)
        response.json.return_value = {'accessToken': fake_jwt(2000000000)}
        with patch('token_manager.requests.get', return_value=response), patch.object(manager, 'validate_token', return_value=(False, 'Temporary outage')):
            manager.renew_token()
        saved.assert_called_once_with(fake_jwt(2000000000))
        self.assertEqual(manager.access_token, fake_jwt(2000000000))
        self.assertFalse(manager.is_token_expired())

    def test_persistence_retry_does_not_renew_token(self):
        saved = Mock(side_effect=[(False, 'disk unavailable'), (True, None)])
        manager = DhanTokenManager('dummy', 'test-client', persist_token_func=saved)
        manager.retry_persistence()
        self.assertFalse(manager.last_persist_ok)
        manager.retry_persistence()
        self.assertTrue(manager.last_persist_ok)


if __name__ == '__main__':
    unittest.main()
