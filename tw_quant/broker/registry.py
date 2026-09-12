from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from .capabilities import BrokerCapabilities
from .identity import BrokerAccountRef
from .instruments import BrokerInstrumentMapper
from .ports import BrokerPort


class BrokerRuntimeState(str, Enum):
    READY = "ready"
    LOCKED = "locked"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class BrokerRegistration:
    account_ref: BrokerAccountRef
    port: BrokerPort
    capabilities: BrokerCapabilities
    instrument_mapper: BrokerInstrumentMapper
    state: BrokerRuntimeState = BrokerRuntimeState.READY

    def __post_init__(self) -> None:
        if self.port.broker_name.strip().lower() != self.account_ref.broker_name:
            raise ValueError("broker port identity does not match registration")


class BrokerRegistry:
    """O(1), exact-target registry assembled before worker startup."""

    def __init__(self) -> None:
        self._registrations: dict[BrokerAccountRef, BrokerRegistration] = {}
        self._frozen = False

    @property
    def registrations(self) -> Mapping[BrokerAccountRef, BrokerRegistration]:
        return MappingProxyType(self._registrations)

    def register(self, registration: BrokerRegistration) -> None:
        if self._frozen:
            raise RuntimeError("broker registry is frozen")
        target = registration.account_ref
        if target in self._registrations:
            raise ValueError(f"duplicate broker registration: {target}")
        self._registrations[target] = registration

    def freeze(self) -> None:
        self._frozen = True

    def registration(self, target: BrokerAccountRef) -> BrokerRegistration:
        try:
            return self._registrations[target]
        except KeyError as exc:
            raise KeyError(f"unknown broker execution target: {target}") from exc

    def resolve(self, target: BrokerAccountRef) -> BrokerPort:
        registration = self.registration(target)
        if registration.state is not BrokerRuntimeState.READY:
            raise RuntimeError(
                f"broker execution target is {registration.state.value}: {target}"
            )
        return registration.port
