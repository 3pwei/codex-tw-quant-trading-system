import unittest
from unittest.mock import patch

from tw_quant.live.settings import LiveSettings


class LiveSettingsTests(unittest.TestCase):
    def test_cloudflare_mode_requires_team_domain_and_audience(self):
        settings = LiveSettings(access_mode="cloudflare")
        with self.assertRaisesRegex(ValueError, "CF_ACCESS_TEAM_DOMAIN"):
            settings.validate()

    def test_cloudflare_access_settings_load_from_environment(self):
        environment = {
            "MARKET_ACCESS_MODE": "cloudflare",
            "CF_ACCESS_TEAM_DOMAIN": "example.cloudflareaccess.com",
            "CF_ACCESS_AUD": "audience-tag",
        }
        with patch.dict("os.environ", environment, clear=True):
            settings = LiveSettings.from_env()
        settings.validate()
        self.assertEqual(settings.access_mode, "cloudflare")
        self.assertEqual(
            settings.cloudflare_access_team_domain,
            "example.cloudflareaccess.com",
        )
        self.assertEqual(settings.cloudflare_access_audience, "audience-tag")

    def test_history_settings_load_and_validate(self):
        with patch.dict("os.environ", {
            "MARKET_HISTORY_DAYS": "14",
            "MARKET_HISTORY_LIMIT": "800",
        }, clear=True):
            settings = LiveSettings.from_env()
        settings.validate()
        self.assertEqual(settings.history_days, 14)
        self.assertEqual(settings.history_limit, 800)

        with self.assertRaisesRegex(ValueError, "MARKET_HISTORY_DAYS"):
            LiveSettings(history_days=31).validate()

    def test_market_data_settings_are_composed_not_flattened(self):
        with patch.dict("os.environ", {
            "MARKET_DATA_PROVIDER": "replay",
            "MARKET_SYMBOL": "TMF",
        }, clear=True):
            settings = LiveSettings.from_env()
        self.assertEqual(settings.market_data.provider, "replay")
        self.assertEqual(settings.market_data.symbol, "TMF")
        # Compatibility properties keep existing deployments and callers valid.
        self.assertEqual(settings.mode, "mock")
        self.assertEqual(settings.symbol, "TMF")


if __name__ == "__main__":
    unittest.main()
