from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CanonicalInstrument:
    symbol: str
    contract: str

    def __post_init__(self) -> None:
        if not self.symbol.strip() or not self.contract.strip():
            raise ValueError("canonical symbol and contract are required")


class BrokerInstrumentMapper(Protocol):
    """Translate canonical instruments at the adapter boundary."""

    def to_broker_contract(self, instrument: CanonicalInstrument) -> str: ...

    def to_canonical_instrument(
        self, broker_contract: str
    ) -> CanonicalInstrument: ...


class LockedInstrumentMapper:
    """Fail-closed mapper used until a production adapter supplies one."""

    def to_broker_contract(self, instrument: CanonicalInstrument) -> str:
        raise RuntimeError("production instrument mapping is not implemented")

    def to_canonical_instrument(
        self, broker_contract: str
    ) -> CanonicalInstrument:
        raise RuntimeError("production instrument mapping is not implemented")
