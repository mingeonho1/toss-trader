from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parent.parent / "scripts" / "analyze_microstructure.py"
    spec = importlib.util.spec_from_file_location("analyze_microstructure", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class MicrostructureAnalysisTest(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = _load_module()

    def test_parse_orderbook_bid_ask_levels(self) -> None:
        raw = {
            "bids": [
                {"price": "100.00", "quantity": "10"},
                {"price": "99.90", "quantity": "5"},
            ],
            "asks": [
                {"price": "100.10", "quantity": "3"},
                {"price": "100.20", "quantity": "2"},
            ],
        }

        parsed = self.mod.parse_orderbook(raw)

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["best_bid"], 100.0)
        self.assertEqual(parsed["best_ask"], 100.1)
        self.assertAlmostEqual(parsed["mid"], 100.05)
        self.assertAlmostEqual(parsed["imbalance"], 0.5)

    def test_parse_orderbook_nested_levels(self) -> None:
        raw = {
            "result": {
                "buyLevels": [{"bidPrice": "50", "bidQuantity": "4"}],
                "sellLevels": [{"askPrice": "51", "askQuantity": "6"}],
            }
        }

        parsed = self.mod.parse_orderbook(raw)

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["best_bid"], 50.0)
        self.assertEqual(parsed["best_ask"], 51.0)
        self.assertAlmostEqual(parsed["imbalance"], -0.2)

    def test_parse_last_trade_price(self) -> None:
        raw = {"trades": [{"tradePrice": "12.34", "quantity": "10"}]}

        self.assertEqual(self.mod.parse_last_trade_price(raw), 12.34)


if __name__ == "__main__":
    unittest.main()
