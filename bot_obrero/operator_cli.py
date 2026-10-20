"""Single operator entry point for the BOT-CRIPTO research/PAPER toolchain.

This command composes existing, independently tested entry points. It does not
add a live-order path. Financial execution remains PAPER-only and the economic
gate stays NO_TRADE until preregistered prospective evidence proves otherwise.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from . import (
    analysis_cli,
    btc_eth_follow_research,
    prospective_btc_eth_v2,
    prospective_capture_pair,
    virtual_backtest,
)

_STATUS = {
    "mode": "PUBLIC_DATA_RESEARCH_AND_LOCAL_PAPER_ONLY",
    "strategy_decision": "NO_TRADE",
    "v2_protocol": "IMPLEMENTED / NOT RUN",
    "historical_leader_candidate": "ETH_TSMOM_168H",
    "candidate_verdict": "NO_ROBUST_EDGE_FOUND",
    "live_execution": "DISABLED_BY_PROJECT_POLICY",
    "blocking_gates": [
        "No accepted real BTCUSDT + ETHUSDT Kline/bookTicker evidence set covering the preregistered warm-up and 365-day evaluation.",
        "No independent prospective 365-day replay has been completed.",
        "The historical ETH_TSMOM_168H candidate failed the preregistered robustness criteria.",
        "V2 requires a frozen public Spot exchangeInfo filter snapshot captured before T0.",
    ],
    "important_limit": (
        "This status is the versioned project policy, not a live inspection of local evidence. "
        "Synthetic tests or a historical backtest do not clear these gates."
    ),
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bot-cripto",
        description=(
            "One command entry point for BOT-CRIPTO public-data analysis, virtual backtests, "
            "BTC/ETH research, prospective V2 replay and paired capture. No LIVE trading."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="show current strategy and release gates")
    subparsers.add_parser(
        "analyze",
        help="run read-only SMA/EMA/RSI analysis using public Binance Spot data",
        add_help=False,
    )
    subparsers.add_parser(
        "backtest",
        help="run the deterministic virtual EMA/RSI backtest (never sends orders)",
        add_help=False,
    )
    subparsers.add_parser(
        "research-btc-eth",
        help="run paired BTC/ETH historical virtual research; does not run prospective V2",
        add_help=False,
    )
    subparsers.add_parser(
        "capture-24h",
        help="capture and validate public BTC/ETH Kline + bookTicker streams for at least 24 h",
        add_help=False,
    )
    subparsers.add_parser(
        "prospective-v2",
        help="capture pre-T0 public filters or run the registered 365-day economic replay",
        add_help=False,
    )
    return parser


def _print_status() -> int:
    print(json.dumps(_STATUS, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = _parser()
    parsed, forwarded = parser.parse_known_args(arguments)

    if parsed.command == "status":
        if forwarded:
            parser.error("status does not accept additional arguments")
        return _print_status()

    if parsed.command == "analyze":
        return analysis_cli.main(forwarded)
    if parsed.command == "backtest":
        return virtual_backtest.main(forwarded)
    if parsed.command == "research-btc-eth":
        return btc_eth_follow_research.main(forwarded)
    if parsed.command == "capture-24h":
        return prospective_capture_pair.main(["capture", *forwarded])
    if parsed.command == "prospective-v2":
        return prospective_btc_eth_v2.main(forwarded)

    parser.error(f"unsupported command: {parsed.command!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
