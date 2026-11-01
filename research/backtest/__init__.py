"""0DTE / options backtest engine for autonomous-quant-researcher research. See README.md.

Research tooling only: reads market data, writes reports, never places orders.
"""

from research.backtest.data import (
    OPTION_DATA_START,
    AlpacaOptionData,
    Session,
    SessionStore,
    occ_symbol,
)
from research.backtest.engine import (
    BacktestEngine,
    CostModel,
    Result,
    mde_two_arm,
    realized_vol,
    wilson_ci,
)
from research.backtest.structures import (
    Leg,
    Structure,
    bs_price,
    call_credit_spread,
    call_debit_spread,
    long_put,
    put_credit_spread,
    put_debit_spread,
    spx_call_credit_spread,
    spx_call_debit_spread,
    spx_put_credit_spread,
    spx_put_debit_spread,
)

__all__ = [
    "AlpacaOptionData", "BacktestEngine", "CostModel", "Leg",
    "OPTION_DATA_START", "Result", "Session", "SessionStore", "Structure",
    "bs_price", "call_credit_spread", "call_debit_spread", "long_put",
    "mde_two_arm", "occ_symbol", "put_credit_spread", "put_debit_spread",
    "realized_vol", "spx_call_credit_spread", "spx_call_debit_spread",
    "spx_put_credit_spread", "spx_put_debit_spread", "wilson_ci",
]
