"""Export an accepted lab candidate as a TradingView Pine Script v5 strategy.

This is a READ-ONLY research artifact: it translates the frozen rule a
surviving candidate encodes into chartable Pine so a human can eyeball what
the loop actually found. The emitted script places no real orders — TradingView
strategies are simulations, and the lab itself never touches a broker.

The candidate contract translated here is deliberately tiny (see the mission
text the loop proposes against): a LABEL string plus ``signal(symbol,
features)`` built from if/elif/else over feature-vs-constant comparisons,
``and``/``or``, simple alias assignments, and returns of -1/0/1. Anything
outside that grammar raises :class:`PineExportError` — a silent
mistranslation is worse than no export.

Feature semantics mirror ``prepare_bars_universe._feature_rows`` exactly,
expressed with ta.* built-ins over daily bars. The two chart-undefinable
families — ``xs_rank_*`` (cross-sectional) and ``event_*`` (staged macro
calendar) — are emitted as their single-symbol constants, 0.5 and 0.0, with
an inline comment saying why.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from research.experiments.bars_feature_spec import FEATURE_FIXTURE


class PineExportError(Exception):
    """The candidate or run record cannot be translated faithfully."""


# ── Pine feature blocks ────────────────────────────────────────────────────
# Each entry emits `float f_<name> = ...` (plus helper lines) matching the
# computation in prepare_bars_universe._feature_rows. Order of emission
# follows FEATURE_FIXTURE so dependencies (rv_ratio reads f_rv_*) precede.

_HELPERS = (
    "float _ret1 = close / close[1] - 1",
    "float _range = high - low",
)

# ta.stdev(..., false) is the SAMPLE stdev (n-1), matching statistics.stdev.
# ta.highestbars/lowestbars return NEGATIVE offsets, so 19 + offset is the
# in-window index in [0, 19] the lab divides by 19. Tie-breaking differs:
# Python's list.index takes the OLDEST extreme, Pine the newest — noted in the
# script header as an approximation.
FEATURE_PINE: dict[str, tuple[str, ...]] = {
    "ret_1d": ("float f_ret_1d = _ret1",),
    "ret_5d": ("float f_ret_5d = close / close[5] - 1",),
    "ret_10d": ("float f_ret_10d = close / close[10] - 1",),
    "ret_20d": ("float f_ret_20d = close / close[20] - 1",),
    "ret_60d": ("float f_ret_60d = close / close[60] - 1",),
    "oc_ret": ("float f_oc_ret = close / open - 1",),
    "gap_open": ("float f_gap_open = open / close[1] - 1",),
    "range_pos": ("float f_range_pos = _range > 0 ? (close - low) / _range : 0.5",),
    "k_body": ("float f_k_body = _range > 0 ? (close - open) / _range : 0.0",),
    "k_upper": ("float f_k_upper = _range > 0 ? (high - math.max(open, close)) / _range : 0.0",),
    "k_lower": ("float f_k_lower = _range > 0 ? (math.min(open, close) - low) / _range : 0.0",),
    "dist_ma_5": ("float f_dist_ma_5 = close / ta.sma(close, 5) - 1",),
    "dist_ma_20": ("float f_dist_ma_20 = close / ta.sma(close, 20) - 1",),
    "dist_ma_60": ("float f_dist_ma_60 = close / ta.sma(close, 60) - 1",),
    "ma_5_20_spread": ("float f_ma_5_20_spread = ta.sma(close, 5) / ta.sma(close, 20) - 1",),
    "rv_5d": ("float f_rv_5d = ta.stdev(_ret1, 5, false)",),
    "rv_20d": ("float f_rv_20d = ta.stdev(_ret1, 20, false)",),
    "rv_60d": ("float f_rv_60d = ta.stdev(_ret1, 60, false)",),
    "rv_ratio_5_20": ("float f_rv_ratio_5_20 = f_rv_20d != 0 ? f_rv_5d / f_rv_20d : 1.0",),
    # The lab's slope is least-squares per session normalised by the window
    # mean; ta.linreg(x, n, 0) - ta.linreg(x, n, 1) is exactly that slope.
    "trend_slope_20": (
        "float f_trend_slope_20 = ta.sma(close, 20) != 0 "
        "? (ta.linreg(close, 20, 0) - ta.linreg(close, 20, 1)) / ta.sma(close, 20) : 0.0",
    ),
    # R^2 of price vs time is the squared Pearson correlation; bar_index is
    # time up to an irrelevant shift.
    "trend_r2_20": ("float f_trend_r2_20 = nz(math.pow(ta.correlation(bar_index, close, 20), 2), 0.0)",),
    "trend_slope_60": (
        "float f_trend_slope_60 = ta.sma(close, 60) != 0 "
        "? (ta.linreg(close, 60, 0) - ta.linreg(close, 60, 1)) / ta.sma(close, 60) : 0.0",
    ),
    "trend_r2_60": ("float f_trend_r2_60 = nz(math.pow(ta.correlation(bar_index, close, 60), 2), 0.0)",),
    "dist_high_20": ("float f_dist_high_20 = close / ta.highest(close, 20) - 1",),
    "dist_low_20": ("float f_dist_low_20 = close / ta.lowest(close, 20) - 1",),
    "rsv_20": (
        "float _span20 = ta.highest(high, 20) - ta.lowest(low, 20)",
        "float f_rsv_20 = _span20 > 0 ? (close - ta.lowest(low, 20)) / _span20 : 0.5",
    ),
    "high_recency_20": ("float f_high_recency_20 = (19 + ta.highestbars(high, 20)) / 19.0",),
    "low_recency_20": ("float f_low_recency_20 = (19 + ta.lowestbars(low, 20)) / 19.0",),
    "up_frac_20": ("float f_up_frac_20 = math.sum(_ret1 > 0 ? 1.0 : 0.0, 20) / 20",),
    "gain_share_20": (
        "float _gains20 = math.sum(_ret1 > 0 ? _ret1 : 0.0, 20)",
        "float _losses20 = math.sum(_ret1 < 0 ? -_ret1 : 0.0, 20)",
        "float f_gain_share_20 = (_gains20 + _losses20) > 0 ? _gains20 / (_gains20 + _losses20) : 0.5",
    ),
    # Signed run length of same-direction closes, capped at +-10 by the lab's
    # lookback; the recursive form reproduces it bar by bar.
    "streak": (
        "var float _streak = 0.0",
        "float _step = close - close[1]",
        "_streak := _step == 0 ? 0.0 : (_streak != 0 and (_step > 0) != (_streak > 0))"
        " ? math.sign(_step) : math.max(-10.0, math.min(10.0, _streak + math.sign(_step)))",
        "float f_streak = _streak",
    ),
    # Population skewness from raw moments: m3 - 3*m1*m2 + 2*m1^3 over var^1.5.
    "ret_skew_20": (
        "float _sk_m1 = ta.sma(_ret1, 20)",
        "float _sk_m2 = math.sum(math.pow(_ret1, 2), 20) / 20",
        "float _sk_m3 = math.sum(math.pow(_ret1, 3), 20) / 20",
        "float _sk_var = _sk_m2 - _sk_m1 * _sk_m1",
        "float _sk_third = _sk_m3 - 3 * _sk_m1 * _sk_m2 + 2 * math.pow(_sk_m1, 3)",
        "float f_ret_skew_20 = _sk_var > 0 ? _sk_third / math.pow(_sk_var, 1.5) : 0.0",
    ),
    "vol_ratio_5_20": (
        "float f_vol_ratio_5_20 = ta.sma(volume, 20) != 0 ? ta.sma(volume, 5) / ta.sma(volume, 20) : 1.0",
    ),
    "vol_cv_20": (
        "float f_vol_cv_20 = ta.sma(volume, 20) != 0 ? ta.stdev(volume, 20, false) / ta.sma(volume, 20) : 0.0",
    ),
    # The lab pairs the last 19 returns with the same sessions' volumes.
    "corr_ret_vol_20": ("float f_corr_ret_vol_20 = nz(ta.correlation(_ret1, volume, 19), 0.0)",),
    # Python weekday() is Monday=0; Pine dayofweek is Sunday=1.
    "day_of_week": ("float f_day_of_week = dayofweek - 2",),
    "event_today": (
        "// Macro release-day flags come from the lab's staged FOMC/CPI/NFP calendar,",
        "// not from OHLCV -- undefinable on a chart, so emit 0.0 (its value on most sessions).",
        "float f_event_today = 0.0",
    ),
    "event_next_session": (
        "// See f_event_today: staged-calendar fact, not chart-computable.",
        "float f_event_next_session = 0.0",
    ),
    # SmartFit-style pivot-anchored regression channel, mirroring
    # prepare_bars_universe._channel_series (which follows the published Pine
    # v6 source's defaults: pivot 10 on daily, |r| >= 0.50, >= 5 bars,
    # z = 1.96, stdev over n-1). The whole state machine lives under
    # chan_bars; the other chan_* features read the globals it computes, and
    # FEATURE_DEPS orders the emission. The lab caps the anchor at 250 bars
    # for snapshot reproducibility where the original grows unbounded.
    "chan_bars": (
        "var int _ch_last_hi = -1",
        "var int _ch_last_lo = -1",
        "var int _ch_anchor = -1",
        "var bool _ch_prev_bull = false",
        "var bool _ch_prev_bear = false",
        "if not na(ta.pivothigh(high, 10, 10))",
        "    _ch_last_hi := bar_index - 10",
        "if not na(ta.pivotlow(low, 10, 10))",
        "    _ch_last_lo := bar_index - 10",
        "if _ch_anchor < 0",
        "    _ch_anchor := math.max(_ch_last_hi, _ch_last_lo)",
        "else if bar_index - _ch_anchor + 1 > 250",
        "    _ch_anchor := bar_index - 249",
        "int _ch_n = _ch_anchor >= 0 ? bar_index - _ch_anchor + 1 : 0",
        "float f_chan_bars = 0.0",
        "float _ch_slope_n = 0.0",
        "float _ch_r = 0.0",
        "float _ch_z = 0.0",
        "float _ch_brk = 0.0",
        "if _ch_n < 3",
        "    _ch_prev_bull := false",
        "    _ch_prev_bear := false",
        "else",
        "    float _ch_mx = (_ch_n - 1) / 2.0",
        "    float _ch_my = ta.sma(close, _ch_n)",
        "    float _ch_sxx = 0.0",
        "    float _ch_sxy = 0.0",
        "    float _ch_syy = 0.0",
        "    for _j = 0 to _ch_n - 1",
        "        float _ch_dx = _j - _ch_mx",
        "        float _ch_dy = close[_ch_n - 1 - _j] - _ch_my",
        "        _ch_sxx += _ch_dx * _ch_dx",
        "        _ch_sxy += _ch_dx * _ch_dy",
        "        _ch_syy += _ch_dy * _ch_dy",
        "    float _ch_slope = _ch_sxx > 0 ? _ch_sxy / _ch_sxx : 0.0",
        "    float _ch_fit = _ch_my + _ch_slope * ((_ch_n - 1) - _ch_mx)",
        "    float _ch_res = 0.0",
        "    for _j = 0 to _ch_n - 1",
        "        float _ch_e = close[_ch_n - 1 - _j] - (_ch_my + _ch_slope * (_j - _ch_mx))",
        "        _ch_res += _ch_e * _ch_e",
        "    float _ch_sig = math.sqrt(_ch_res / (_ch_n - 1))",
        "    float _ch_zv = _ch_sig > 0 ? (close - _ch_fit) / _ch_sig : 0.0",
        "    _ch_zv := math.max(-10.0, math.min(10.0, _ch_zv))",
        "    f_chan_bars := _ch_n",
        "    _ch_slope_n := _ch_my != 0 ? _ch_slope / _ch_my : 0.0",
        "    _ch_r := _ch_sxx > 0 and _ch_syy > 0 ? _ch_sxy / math.sqrt(_ch_sxx * _ch_syy) : 0.0",
        "    _ch_z := _ch_zv",
        "    bool _ch_ok = _ch_n >= 5 and math.abs(_ch_r) >= 0.5",
        "    bool _ch_bull = _ch_ok and _ch_zv > 1.96",
        "    bool _ch_bear = _ch_ok and _ch_zv < -1.96",
        "    if _ch_bull and not _ch_prev_bull",
        "        _ch_brk := 1.0",
        "    else if _ch_bear and not _ch_prev_bear",
        "        _ch_brk := -1.0",
        "    _ch_prev_bull := _ch_bull",
        "    _ch_prev_bear := _ch_bear",
        "    if _ch_brk != 0.0",
        "        int _ch_cand = _ch_brk > 0 ? _ch_last_lo : _ch_last_hi",
        "        int _ch_fresh = bar_index",
        "        if _ch_cand > _ch_anchor and _ch_cand < bar_index",
        "            int _ch_cn = bar_index - _ch_cand + 1",
        "            float _ch_cmx = (_ch_cn - 1) / 2.0",
        "            float _ch_cmy = ta.sma(close, _ch_cn)",
        "            float _ch_csxx = 0.0",
        "            float _ch_csxy = 0.0",
        "            float _ch_csyy = 0.0",
        "            for _j = 0 to _ch_cn - 1",
        "                float _ch_cdx = _j - _ch_cmx",
        "                float _ch_cdy = close[_ch_cn - 1 - _j] - _ch_cmy",
        "                _ch_csxx += _ch_cdx * _ch_cdx",
        "                _ch_csxy += _ch_cdx * _ch_cdy",
        "                _ch_csyy += _ch_cdy * _ch_cdy",
        "            float _ch_cr = _ch_csxx > 0 and _ch_csyy > 0"
        " ? _ch_csxy / math.sqrt(_ch_csxx * _ch_csyy) : 0.0",
        "            if math.abs(_ch_cr) >= 0.5",
        "                _ch_fresh := _ch_cand",
        "        _ch_anchor := _ch_fresh",
    ),
    "chan_slope": ("float f_chan_slope = _ch_slope_n",),
    "chan_pearson": ("float f_chan_pearson = _ch_r",),
    "chan_z": ("float f_chan_z = _ch_z",),
    "chan_breakout": ("float f_chan_breakout = _ch_brk",),
    # ta.dmi is Wilder's DMI/ADX with RMA smoothing — the same convention the
    # lab computes; only the seed bar differs by one, noted as approximation.
    "adx_14": (
        "[_adx_dip, _adx_dim, _adx_val] = ta.dmi(14, 14)",
        "float f_adx_14 = nz(_adx_val, 0.0)",
    ),
}

_XS_RANK_COMMENT = (
    "// Cross-sectional rank has no single-chart definition; the lab's single-symbol",
    "// sessions make every xs_rank_* exactly 0.5, so emit that constant.",
)

# Features a block depends on (must be emitted earlier — they are, in
# FEATURE_FIXTURE order).
FEATURE_DEPS: dict[str, tuple[str, ...]] = {
    "rv_ratio_5_20": ("rv_5d", "rv_20d"),
    "chan_slope": ("chan_bars",),
    "chan_pearson": ("chan_bars",),
    "chan_z": ("chan_bars",),
    "chan_breakout": ("chan_bars",),
}

# Dataset-native names outside the daily bars family. Some missions prepare
# their own feature sets; keep tiny explicit approximations here rather than
# pretending an unknown name means something.
EXTRA_FEATURE_PINE: dict[str, tuple[str, ...]] = {
    "credit_frac": (
        "// TODO: credit_frac is dataset-native (0DTE credit-spread width as a",
        "// fraction of available strikes) with no OHLCV definition. Set a real",
        "// value -- or wire a real series -- before trusting this chart.",
        'float f_credit_frac = input.float(0.0, "credit_frac placeholder")',
    ),
    "day_of_week": ("float f_day_of_week = dayofweek - 2",),
}

_PINE_RESERVED = frozenset({
    "open", "high", "low", "close", "volume", "time", "hl2", "hlc3", "ohlc4",
    "bar_index", "dayofweek", "na", "true", "false", "if", "else", "var",
    "strategy", "ta", "math", "float", "int", "bool", "sig", "input",
})

_ALIAS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Evidence keys lifted from the runs.jsonl feedback string into the header.
_EVIDENCE_KEYS = ("robust_portfolio_score", "net_trade_mean_bps", "trades", "gate_penalty")

_COMPARE_FLIP = {
    ast.Lt: ast.Gt, ast.Gt: ast.Lt, ast.LtE: ast.GtE, ast.GtE: ast.LtE,
    ast.Eq: ast.Eq, ast.NotEq: ast.NotEq,
}
_PINE_OPS = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.Eq: "==", ast.NotEq: "!="}


@dataclass
class _Compiled:
    label: str
    used_features: set[str] = field(default_factory=set)
    alias_lines: list[str] = field(default_factory=list)
    decision_lines: list[str] = field(default_factory=list)


# ── AST translation ────────────────────────────────────────────────────────

def _numeric(node: ast.expr) -> float | int | None:
    """A numeric constant, possibly negated; None when the node is not one."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        inner = _numeric(node.operand)
        if inner is None:
            return None
        return -inner if isinstance(node.op, ast.USub) else inner
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            raise PineExportError("boolean constants are not part of the candidate contract")
        if isinstance(node.value, (int, float)):
            return node.value
    return None


def _feature_ref(node: ast.expr, features_arg: str) -> str | None:
    """The feature name of features['NAME'] / features.get('NAME'[, default])."""
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) \
            and node.value.id == features_arg and isinstance(node.slice, ast.Constant) \
            and isinstance(node.slice.value, str):
        return node.slice.value
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr == "get" and isinstance(node.func.value, ast.Name) \
            and node.func.value.id == features_arg and 1 <= len(node.args) <= 2 \
            and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str) \
            and not node.keywords:
        if len(node.args) == 2 and _numeric(node.args[1]) is None:
            raise PineExportError("features.get() default must be a numeric constant")
        return node.args[0].value
    return None


def _pine_name(feature: str, used: set[str]) -> str:
    if feature in FEATURE_PINE or (feature.startswith("xs_rank_") and feature in FEATURE_FIXTURE):
        used.add(feature)
        return f"f_{feature}"
    if feature in EXTRA_FEATURE_PINE:
        used.add(feature)
        return f"f_{feature}"
    raise PineExportError(
        f"unknown feature {feature!r}: not in the daily bars family and no "
        "dataset-native mapping exists in EXTRA_FEATURE_PINE"
    )


class _SignalTranslator:
    def __init__(self, features_arg: str):
        self.features_arg = features_arg
        self.aliases: dict[str, ast.expr] = {}
        self.used: set[str] = set()

    def expression(self, node: ast.expr) -> str:
        if isinstance(node, ast.BoolOp):
            if not isinstance(node.op, (ast.And, ast.Or)):
                raise PineExportError("only and/or boolean operators are supported")
            joiner = " and " if isinstance(node.op, ast.And) else " or "
            parts = []
            for value in node.values:
                text = self.expression(value)
                # Parenthesise nested boolops so Pine precedence never
                # silently regroups the candidate's logic.
                if isinstance(value, ast.BoolOp) and not isinstance(value.op, type(node.op)):
                    text = f"({text})"
                parts.append(text)
            return joiner.join(parts)
        if isinstance(node, ast.Compare):
            return self._comparison(node)
        if isinstance(node, ast.Name) and node.id in self.aliases:
            return self.expression(self.aliases[node.id])
        raise PineExportError(
            f"unsupported expression in signal(): {ast.dump(node)[:120]}"
        )

    def _comparison(self, node: ast.Compare) -> str:
        if len(node.ops) != 1 or len(node.comparators) != 1:
            raise PineExportError("chained comparisons (a < b < c) are not supported")
        op = type(node.ops[0])
        if op not in _PINE_OPS:
            raise PineExportError(f"unsupported comparison operator {op.__name__}")
        left = self._classify(node.left)
        right = self._classify(node.comparators[0])
        if left is None or right is None:
            raise PineExportError(
                "comparisons must be between a feature reference and a numeric constant"
            )
        if left[0] == "const" and right[0] == "const":
            raise PineExportError("constant-vs-constant comparison is dead logic, refusing")
        if left[0] == "feature" and right[0] == "feature":
            raise PineExportError("feature-vs-feature comparisons are not supported")
        if left[0] == "feature":
            return f"{left[1]} {_PINE_OPS[op]} {_fmt_num(right[1])}"
        return f"{right[1]} {_PINE_OPS[_COMPARE_FLIP[op]]} {_fmt_num(left[1])}"

    def _classify(self, node: ast.expr) -> tuple[str, object] | None:
        """('feature', pine text) or ('const', number); None when neither.

        An alias keeps its NAME in the output when it stands for a feature
        (the alias line is emitted separately) and folds to its number when it
        stands for a constant.
        """
        if isinstance(node, ast.Name) and node.id in self.aliases:
            target = self.aliases[node.id]
            num = _numeric(target)
            if num is not None:
                return ("const", num)
            name = _feature_ref(target, self.features_arg)
            if name is not None:
                _pine_name(name, self.used)
                return ("feature", node.id)
            return None
        num = _numeric(node)
        if num is not None:
            return ("const", num)
        name = _feature_ref(node, self.features_arg)
        if name is not None:
            return ("feature", _pine_name(name, self.used))
        return None

    def alias(self, stmt: ast.Assign) -> str:
        if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
            raise PineExportError("only single-name assignments are supported")
        target = stmt.targets[0].id
        if not _ALIAS_RE.match(target) or target in _PINE_RESERVED:
            raise PineExportError(f"alias name {target!r} collides with a Pine built-in")
        if target in self.aliases:
            raise PineExportError(f"alias {target!r} assigned twice")
        num = _numeric(stmt.value)
        if num is not None:
            self.aliases[target] = stmt.value
            return f"float {target} = {_fmt_num(num)}"
        name = _feature_ref(stmt.value, self.features_arg)
        if name is None:
            raise PineExportError(
                f"alias {target!r} must be a feature reference or a numeric constant"
            )
        self.aliases[target] = stmt.value
        return f"float {target} = {_pine_name(name, self.used)}"

    def decision(self, stmts: list[ast.stmt]) -> "int | tuple":
        if not stmts:
            raise PineExportError("signal() has a code path that falls off without returning")
        head, *tail = stmts
        if isinstance(head, ast.Return):
            if tail:
                raise PineExportError("statements after a return are unreachable, refusing")
            return self._return_value(head.value)
        if isinstance(head, ast.If):
            then = self.decision(head.body)
            other = self.decision(head.orelse) if head.orelse else self.decision(tail)
            return (head.test, then, other)
        raise PineExportError(
            f"unsupported statement in signal(): {type(head).__name__}; only if/return "
            "after alias assignments are translatable"
        )

    def _return_value(self, node: ast.expr | None) -> int:
        value = _numeric(node) if node is not None else None
        if isinstance(value, int) and value in (-1, 0, 1):
            return value
        if isinstance(value, float) and value in (-1.0, 0.0, 1.0):
            return int(value)
        raise PineExportError("signal() may only return the int literals -1, 0, or 1")


def _fmt_num(value: float | int) -> str:
    if isinstance(value, int):
        return str(value)
    return repr(value)


def compile_candidate(source: str) -> _Compiled:
    """Translate candidate source into Pine fragments. Raises PineExportError."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise PineExportError(f"candidate does not parse: {exc}") from exc

    label: str | None = None
    func: ast.FunctionDef | None = None
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            continue  # module docstring
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "LABEL" \
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            label = node.value.value
            continue
        if isinstance(node, ast.FunctionDef) and node.name == "signal":
            if func is not None:
                raise PineExportError("signal() defined twice")
            func = node
            continue
        raise PineExportError(
            f"unsupported module-level statement: {type(node).__name__}; a candidate "
            "carries only a docstring, LABEL, and signal()"
        )
    if label is None:
        raise PineExportError("candidate has no LABEL string")
    if func is None:
        raise PineExportError("candidate defines no signal() function")
    if len(func.args.args) != 2:
        raise PineExportError("signal() must take exactly (symbol, features)")
    features_arg = func.args.args[1].arg

    translator = _SignalTranslator(features_arg)
    compiled = _Compiled(label=label)
    body = list(func.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body.pop(0)  # docstring
    while body and isinstance(body[0], ast.Assign):
        compiled.alias_lines.append(translator.alias(body.pop(0)))
    if not body:
        raise PineExportError("signal() has no decision logic after its assignments")

    # Render tests AFTER aliases so alias resolution is available.
    tree_node = translator.decision(body)

    def _render_with_tests(node, level, first=True):
        if isinstance(node, int):
            return ["    " * level + str(node)]
        test, then, other = node
        pad = "    " * level
        head = ("if " if first else "else if ") + translator.expression(test)
        lines = [pad + head] + _render_with_tests(then, level + 1)
        if isinstance(other, int):
            lines += [pad + "else", "    " * (level + 1) + str(other)]
        else:
            lines += _render_with_tests(other, level, first=False)
        return lines

    compiled.decision_lines = _render_with_tests(tree_node, 0)
    compiled.used_features = translator.used
    return compiled


# ── run-record lookup and evidence ─────────────────────────────────────────

def _find_run_record(run_dir: Path, candidate_hash: str | None) -> dict:
    ledger = run_dir / "state" / "runs.jsonl"
    if not ledger.is_file():
        raise PineExportError(f"no runs ledger at {ledger}")
    chosen: dict | None = None
    for line in ledger.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if candidate_hash is not None:
            if record.get("candidate_hash") == candidate_hash and record.get("feedback"):
                chosen = record
        elif record.get("accepted") and record.get("candidate_hash") and record.get("feedback"):
            chosen = record  # last accepted wins: later survivors superseded earlier
    if chosen is None:
        what = f"hash {candidate_hash}" if candidate_hash else "an accepted candidate"
        raise PineExportError(f"no feedback record for {what} in {ledger}")
    return chosen


def _parse_evidence(feedback: str) -> dict[str, str]:
    evidence: dict[str, str] = {}
    for key in _EVIDENCE_KEYS:
        match = re.search(rf"\b{re.escape(key)}=([^;\s]+)", feedback)
        if match:
            evidence[key] = match.group(1)
    return evidence


# ── Pine assembly ──────────────────────────────────────────────────────────

def _feature_blocks(used: set[str]) -> list[str]:
    names = set(used)
    for name in used:
        names.update(FEATURE_DEPS.get(name, ()))
    lines: list[str] = []
    for name in FEATURE_FIXTURE:  # fixture order keeps deps before dependents
        if name not in names:
            continue
        if name.startswith("xs_rank_"):
            lines += [*_XS_RANK_COMMENT, f"float f_{name} = 0.5"]
        else:
            lines += FEATURE_PINE[name]
    for name in sorted(names):
        if name not in FEATURE_FIXTURE:
            lines += EXTRA_FEATURE_PINE[name]
    return lines


def render_pine(*, compiled: _Compiled, mission: str, candidate_hash: str,
                evidence: dict[str, str]) -> str:
    title = compiled.label.replace('"', "'")
    evidence_text = "  ".join(f"{key}={value}" for key, value in evidence.items()) \
        or "(no evidence fields found in the feedback line)"
    blocks = _feature_blocks(compiled.used_features)
    feature_lines: list[str] = []
    joined = "\n".join(blocks)
    if "_ret1" in joined:
        feature_lines.append("float _ret1 = close / close[1] - 1")
    if "_range" in joined:
        feature_lines.append("float _range = high - low")
    feature_lines += blocks

    decision = list(compiled.decision_lines)
    decision[0] = "int sig = " + decision[0]

    lines = [
        "//@version=5",
        f'strategy("{title} [autonomous-quant-researcher]", overlay=true, initial_capital=100000,',
        "     default_qty_type=strategy.percent_of_equity, default_qty_value=100)",
        "",
        "// -- autonomous-quant-researcher lab export " + "-" * 52,
        f"// Candidate: {compiled.label}",
        f"// Mission: {mission}",
        f"// Candidate hash: {candidate_hash}",
        "// Evidence (from the run's state/runs.jsonl feedback):",
        f"//   {evidence_text}",
        "// Horizon: the lab measured a 1-session open-to-close payoff -- a signal at",
        "// session t's close is paid by session t+1's open-to-close. Pine entries",
        "// fill at the next bar's open, the closest daily-chart approximation.",
        "// The first ~60 bars are feature warm-up (na) and produce no trades,",
        "// matching the lab's 61-session warm-up. high/low_recency_20 differs on",
        "// ties only: the lab takes the oldest extreme, Pine the newest.",
        "",
        "// -- features (mirror prepare_bars_universe._feature_rows) --",
        *feature_lines,
        "",
        "// -- signal (translated from candidate.py) --",
        *compiled.alias_lines,
        *decision,
        "",
        "// -- entries / exits --",
        "bool open_long = sig == 1 and sig != sig[1]",
        "bool open_short = sig == -1 and sig != sig[1]",
        "bool go_flat = sig == 0 and sig[1] != 0",
        "if open_long",
        '    strategy.entry("L", strategy.long)',
        "if open_short",
        '    strategy.entry("S", strategy.short)',
        "if go_flat",
        '    strategy.close_all(comment="sig flat")',
        "// Opposite entries reverse the open position (pyramiding=0), covering flips.",
        "",
        "plotshape(open_long, title=\"long\", style=shape.triangleup, location=location.belowbar,",
        "     color=color.green, size=size.small)",
        "plotshape(open_short, title=\"short\", style=shape.triangledown, location=location.abovebar,",
        "     color=color.red, size=size.small)",
        "",
    ]
    return "\n".join(lines)


def export(run_dir: Path, candidate_hash: str | None = None, out: Path | None = None) -> str:
    """Translate the accepted (or named) candidate in ``run_dir`` to Pine v5."""
    run_dir = Path(run_dir)
    record = _find_run_record(run_dir, candidate_hash)
    resolved_hash = record["candidate_hash"]
    candidate_path = run_dir / "workspace" / "candidates" / resolved_hash / "candidate.py"
    if not candidate_path.is_file():
        raise PineExportError(f"candidate source not found at {candidate_path}")
    compiled = compile_candidate(candidate_path.read_text(encoding="utf-8"))
    pine = render_pine(
        compiled=compiled,
        mission=run_dir.name,
        candidate_hash=resolved_hash,
        evidence=_parse_evidence(record.get("feedback", "")),
    )
    if out is not None:
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(pine, encoding="utf-8")
    return pine


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dir", type=Path, help=".research/runs/<slug> directory")
    parser.add_argument("--candidate-hash", default=None,
                        help="export this candidate instead of the accepted one")
    parser.add_argument("--out", type=Path, default=None, help="also write the Pine source here")
    args = parser.parse_args(argv)
    pine = export(args.run_dir, args.candidate_hash, args.out)
    if args.out:
        print(f"wrote {args.out} ({len(pine.splitlines())} lines)")
    else:
        print(pine)


if __name__ == "__main__":
    main()
