from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path

from .config import ExecutionServiceSettings
from .runtime import build_execution_service


def _healthcheck(settings: ExecutionServiceSettings) -> int:
    path = Path(settings.health_path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        heartbeat = datetime.fromisoformat(str(document["heartbeat_at"]))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return 1
    age = (datetime.now(timezone.utc) - heartbeat).total_seconds()
    safe_state = (
        document.get("locked") is True
        and document.get("execution_state") in {"disabled", "locked"}
        and document.get("external_order_calls") == 0
    )
    return 0 if safe_state and age <= max(30.0, settings.heartbeat_seconds * 4) else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=("run", "validate", "healthcheck"),
        nargs="?",
        default="run",
    )
    args = parser.parse_args()
    settings = ExecutionServiceSettings.from_env()
    if args.command == "healthcheck":
        return _healthcheck(settings)
    runtime = build_execution_service(settings)
    if args.command == "validate":
        print(json.dumps(runtime.public_health(), sort_keys=True))
        asyncio.run(runtime.close())
        return 0
    try:
        asyncio.run(runtime.serve())
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
