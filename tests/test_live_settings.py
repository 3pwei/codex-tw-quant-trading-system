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


if __name__ == "__main__":
    unittest.main()
