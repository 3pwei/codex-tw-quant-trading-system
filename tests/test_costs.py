import unittest

from tw_quant.config import CostConfig
from tw_quant.costs import TaiwanStockCostModel


class CostModelTests(unittest.TestCase):
    def test_sell_side_includes_tax(self):
        model = TaiwanStockCostModel(
            CostConfig(slippage_bps=0, round_cost_to_ntd=True)
        )
        buy = model.order_cost(100, 1_000, "buy")
        sell = model.order_cost(100, 1_000, "sell")
        self.assertEqual(buy.commission, 142)
        self.assertEqual(buy.tax, 0)
        self.assertEqual(sell.commission, 142)
        self.assertEqual(sell.tax, 150)

    def test_minimum_commission(self):
        model = TaiwanStockCostModel(CostConfig(slippage_bps=0))
        self.assertEqual(model.order_cost(10, 1, "buy").commission, 20)


if __name__ == "__main__":
    unittest.main()

