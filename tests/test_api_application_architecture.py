from __future__ import annotations

import unittest
from pathlib import Path
from typing import cast

from tw_quant.live.api_errors import application_http_error
from tw_quant.live.application import (
    BadRequestError,
    InvalidInputError,
    ResourceConflictError,
    ResourceGoneError,
    ResourceNotFoundError,
    ReplayOrderInput,
    ResearchApplicationService,
    ServiceUnavailableError,
)
from tw_quant.live.storage import BarRepository
from tw_quant.replay import ReplayTradingSessionRegistry


ROOT = Path(__file__).resolve().parents[1]


class ApiApplicationArchitectureTests(unittest.TestCase):
    def test_application_layer_has_no_fastapi_dependency(self) -> None:
        application_root = ROOT / "tw_quant" / "live" / "application"
        for path in application_root.glob("*.py"):
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertNotIn("import fastapi", source)
                self.assertNotIn("from fastapi", source)

    def test_use_case_routers_do_not_access_persistence_directly(self) -> None:
        route_root = ROOT / "tw_quant" / "live" / "api_routes"
        for name in ("paper.py", "research.py", "strategies.py"):
            with self.subTest(route=name):
                source = (route_root / name).read_text(encoding="utf-8")
                self.assertNotIn("deps.repo", source)
                self.assertNotIn("deps.replay_trading", source)
                self.assertNotIn("deps.paper.", source)

    def test_application_errors_have_stable_http_mapping(self) -> None:
        cases = (
            (BadRequestError("bad"), 400),
            (ResourceNotFoundError("missing"), 404),
            (ResourceConflictError("conflict"), 409),
            (ResourceGoneError("gone"), 410),
            (InvalidInputError("invalid"), 422),
            (ServiceUnavailableError("unavailable"), 503),
        )
        for error, expected_status in cases:
            with self.subTest(error=type(error).__name__):
                response = application_http_error(error)
                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(response.detail, str(error))

    def test_invalid_replay_command_stays_an_input_error(self) -> None:
        sessions = ReplayTradingSessionRegistry()
        service = ResearchApplicationService(
            cast(BarRepository, object()), sessions, "TMF"
        )
        try:
            with self.assertRaises(InvalidInputError):
                service.submit_replay_order(
                    "missing",
                    ReplayOrderInput(
                        strategy_id="manual",
                        strategy_version=0,
                        side="buy",
                        quantity=1,
                        stop_loss_price=None,
                        reduce_only=False,
                    ),
                    "request-1",
                    "owner-1",
                )
        finally:
            sessions.close()


if __name__ == "__main__":
    unittest.main()
