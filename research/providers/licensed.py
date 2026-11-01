"""Licensed-data provider boundary: declared schemas, pluggable adapters.

Some campaigns need data that is not free: point-in-time consensus estimate
history, or a multi-year daily GPU rental price history. Those products are
licensed, so this repository ships the *interface* and no client.

A licensed provider declares the exact record shape it will serve, reads its
credential from an environment variable by name only, and refuses every
request with a typed error until a deployment injects a licensed adapter that
honours the same schema. It never returns a placeholder row and never logs a
credential.

This is deliberately generic: the campaign code depends on the schema and the
availability contract, not on any particular vendor. For a free/public
example that *does* return rows, see :mod:`research.providers.public_csv`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from datetime import date
from enum import StrEnum


class EstimateMeasure(StrEnum):
    REVENUE = "revenue"
    EPS = "eps"
    CAPEX = "capex"


class FiscalPeriod(StrEnum):
    FY1 = "FY+1"  # the next fiscal year not yet reported as of the as-of date


@dataclass(frozen=True)
class ConsensusEstimateRecord:
    """One point-in-time consensus observation. The schema is the contract."""

    symbol: str
    as_of: date                 # the date this consensus was known; never backfilled
    measure: EstimateMeasure
    fiscal_period: FiscalPeriod
    fiscal_year_end: date       # pins the target year so a period roll is visible
    consensus_mean: float
    contributor_count: int
    currency: str
    provider: str
    provider_record_id: str


ESTIMATE_SCHEMA: tuple[tuple[str, str], ...] = tuple(
    (item.name, str(item.type)) for item in fields(ConsensusEstimateRecord)
)


@dataclass(frozen=True)
class GpuRentalRecord:
    """One daily GPU rental price observation from a licensed history."""

    gpu: str                    # "H100", "H200" or "B200"
    day: date
    usd_per_gpu_hour: float
    methodology: str            # the vendor's methodology identifier for that day
    provider: str
    provider_record_id: str


GPU_RENTAL_SCHEMA: tuple[tuple[str, str], ...] = tuple(
    (item.name, str(item.type)) for item in fields(GpuRentalRecord)
)


class ProviderNotAvailable(RuntimeError):
    """The provider cannot serve rows in this deployment."""

    def __init__(self, provider: str, reason: str, *, credential_present: bool) -> None:
        self.provider = provider
        self.reason = reason
        self.credential_present = credential_present
        super().__init__(f"{provider}: {reason}")


class ProviderNotLicensed(ProviderNotAvailable):
    """No credential for the provider is configured."""


@dataclass(frozen=True)
class LicensedProviderStub:
    """The pluggable licensed-provider contract.

    A deployment replaces this with a concrete adapter that implements
    ``fetch`` for the declared schema. The stub itself never returns rows.
    """

    name: str
    product: str
    credential_env: str
    schema: tuple[tuple[str, str], ...]

    @property
    def credential_present(self) -> bool:
        return bool(os.environ.get(self.credential_env, "").strip())

    def availability(self) -> dict[str, object]:
        present = self.credential_present
        return {
            "provider": self.name,
            "product": self.product,
            "credential_env": self.credential_env,
            "credential_present": present,
            "schema": [list(item) for item in self.schema],
            "client_bundled": False,
            "available": False,
            "reason": ("credential is set, but no licensed client is bundled in this repository"
                       if present else "not licensed: no credential configured"),
        }

    def fetch(self, keys: tuple[str, ...], start: date, end: date) -> tuple[object, ...]:
        """Refuse. ``keys`` are symbols for estimates and GPU names for rentals."""
        if not keys or end < start:
            raise ValueError("fetch requires keys and a non-empty date range")
        if not self.credential_present:
            raise ProviderNotLicensed(self.name, "not licensed: no credential configured",
                                      credential_present=False)
        raise ProviderNotAvailable(
            self.name, "no licensed client is bundled in this repository",
            credential_present=True,
        )


# Two generic estimate slots, in case a deployment wants a primary and an
# independent second opinion. Neither names a vendor.
CONSENSUS_PRIMARY = LicensedProviderStub(
    name="consensus_primary",
    product="point-in-time consensus estimates (primary licensed adapter)",
    credential_env="CONSENSUS_PRIMARY_API_KEY", schema=ESTIMATE_SCHEMA,
)
CONSENSUS_SECONDARY = LicensedProviderStub(
    name="consensus_secondary",
    product="point-in-time consensus estimates (independent licensed adapter)",
    credential_env="CONSENSUS_SECONDARY_API_KEY", schema=ESTIMATE_SCHEMA,
)
GPU_RENTAL_HISTORY = LicensedProviderStub(
    name="gpu_rental_history", product="licensed multi-year daily GPU rental price history",
    credential_env="GPU_RENTAL_HISTORY_API_KEY", schema=GPU_RENTAL_SCHEMA,
)
ESTIMATE_PROVIDERS = (CONSENSUS_PRIMARY, CONSENSUS_SECONDARY)
