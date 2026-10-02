"""Manual one-shot Binance Spot integration check for FASE 1.11.

This script is deliberately outside pytest/CI. It makes exactly one public request,
without credentials, and exercises the real adapter through MarketData.
"""

from bot_obrero.acquisition import InstrumentMapper, InstrumentMappingRule
from bot_obrero.binance_spot import BinanceSpotRestAdapter
from bot_obrero.market_data import MarketData, InstrumentIdentity

MAPPER = InstrumentMapper(
    [
        InstrumentMappingRule(
            provider="binance",
            provider_symbol="BTCUSDT",
            provider_market="SPOT",
            provider_venue="BINANCE",
            instrument=InstrumentIdentity(
                "btc-usdt-spot",
                "BTC/USDT",
                "SPOT",
            ),
        )
    ]
)


def main() -> int:
    adapter = BinanceSpotRestAdapter(instrument_mapper=MAPPER)
    item = adapter.fetch_one(symbol="BTCUSDT", interval="1m", limit=1)
    assert isinstance(item, MarketData)
    assert item.source.provider == "binance"
    assert item.source.venue == "BINANCE"
    assert item.source.source_id == "binance-spot-rest"
    assert item.instrument.instrument_id == "btc-usdt-spot"
    assert item.available_at is None
    print(
        "BINANCE LIVE INTEGRATION OK",
        f"market_data_id={item.market_data_id}",
        f"observed_at={item.observed_at.isoformat()}",
        f"received_at={item.received_at.isoformat()}",
        f"available_at={item.available_at}",
        f"candle_state={item.payload.candle_state.value}",
        f"quality={item.quality.value}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
