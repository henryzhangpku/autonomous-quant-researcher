"""Consensus-estimate provider boundary: declared schema, no bundled client.

Campaign 1 of ``research/missions/gpu-leads-revisions`` needs point-in-time
consensus estimate history (what the consensus was on each date, as it was
known that day, never a later restatement). That history is licensed vendor
data — S&P Capital IQ or LSEG I/B/E/S — and no licensed client is bundled in
this repository.

So these providers declare the exact record shape the campaign will consume
and then refuse, with a typed error, every request for rows. They never
return a row. A deployment that holds a licence injects a real adapter that
honours the same schema; it is never filled with placeholder values here.

Credentials, if a deployment supplies them, are read from the environment by
name only and never logged, echoed or stored.
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


CAPITAL_IQ = LicensedProviderStub(
    name="capital_iq", product="S&P Capital IQ consensus estimates (point-in-time)",
    credential_env="CAPIQ_API_KEY", schema=ESTIMATE_SCHEMA,
)
LSEG_IBES = LicensedProviderStub(
    name="lseg_ibes", product="LSEG I/B/E/S consensus estimates (point-in-time)",
    credential_env="LSEG_API_KEY", schema=ESTIMATE_SCHEMA,
)
GPU_RENTAL_HISTORY = LicensedProviderStub(
    name="gpu_rental_history", product="licensed multi-year daily GPU rental price history",
    credential_env="GPU_RENTAL_HISTORY_API_KEY", schema=GPU_RENTAL_SCHEMA,
)
ESTIMATE_PROVIDERS = (CAPITAL_IQ, LSEG_IBES)
