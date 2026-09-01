import unittest

from orders import apply_discount, line_total, round_money, shipping_cost, tax_rate


class PricingBaselineTest(unittest.TestCase):
    def test_round_money_rounds_to_cents(self):
        self.assertEqual(round_money(1.005), 1.01)
        self.assertEqual(round_money(1.234), 1.23)

    def test_apply_discount_matches_expected_cents(self):
        self.assertEqual(apply_discount(100.0, 10), 90.0)
        self.assertEqual(apply_discount(99.99, 25), 74.99)
        self.assertEqual(apply_discount(1000.0, 0), 1000.0)

    def test_discount_rejects_invalid_inputs(self):
        with self.assertRaises(ValueError):
            apply_discount(-1, 10)
        with self.assertRaises(ValueError):
            apply_discount(10, 101)

    def test_tax_rate_known_countries(self):
        self.assertEqual(tax_rate("us"), 0.08)
        self.assertEqual(tax_rate("DE"), 0.19)
        self.assertEqual(tax_rate("JP"), 0.10)

    def test_tax_rate_rejects_unknown_country(self):
        with self.assertRaises(KeyError):
            tax_rate("XX")

    def test_shipping_cost_weight_tiers_and_remote_surcharge(self):
        self.assertEqual(shipping_cost(500), 4.0)
        self.assertEqual(shipping_cost(2500), 8.0)
        self.assertEqual(shipping_cost(6000), 12.0)
        self.assertEqual(shipping_cost(500, remote_zip=True), 10.0)

    def test_shipping_cost_rejects_negative_weight(self):
        with self.assertRaises(ValueError):
            shipping_cost(-1)

    def test_line_total_combines_quantity_and_discount(self):
        self.assertEqual(line_total(12.5, 2), 25.0)
        self.assertEqual(line_total(12.5, 2, 10), 22.5)
        self.assertEqual(line_total(12.5, 0), 0.0)

    def test_line_total_rejects_negative_quantity(self):
        with self.assertRaises(ValueError):
            line_total(1.0, -1)


if __name__ == "__main__":
    unittest.main()
