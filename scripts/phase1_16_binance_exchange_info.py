"""Manual Phase 1.16 Binance Spot ExchangeInfo integration check.

This script performs one public metadata request for BTCUSDT. It is intentionally
not imported by pytest/CI and performs no market-data download.
"""

from bot_obrero.binance_instruments import BinanceSpotInstrumentMetadata


def main() -> None:
    metadata = BinanceSpotInstrumentMetadata()
    record = metadata.fetch("BTCUSDT")
    print("BINANCE EXCHANGEINFO OK")
    print(f"provider_symbol={record.provider_symbol}")
    print(f"base_asset={record.base_asset}")
    print(f"quote_asset={record.quote_asset}")
    print(f"market={record.market}")
    print(f"venue={record.venue}")
    print(f"status={record.status}")
    print(f"is_spot_trading_allowed={record.is_spot_trading_allowed}")


if __name__ == "__main__":
    main()
