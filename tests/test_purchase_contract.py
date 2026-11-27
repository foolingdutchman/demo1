import unittest

import purchase_contract


class PurchaseContractTests(unittest.TestCase):
    def test_contract_uses_the_matching_product_specification(self):
        cases = (
            ("cotton", "Staple: 29mm", "Purity:"),
            ("sesame", "Purity: 98", "Staple:"),
            ("peanut", "Alfatoxin: 3", "Protein:"),
            ("peanut_meal", "Protein: 47", "Purity:"),
        )
        order = {
            "id": 12,
            "created_at": "2026-11-27 10:00:00",
            "quotation_date": "2026-11-27",
            "crop_year": "2024/25",
            "length_mm": 29,
            "micronaire": 4.2,
            "strength_min": 28,
            "purity_percent": 98,
            "oil_percent": 50,
            "moisture_percent": 7,
            "ffa_percent": 1.5,
            "aflatoxin_ppb": 3,
            "protein_percent": 47,
            "quantity_tons": 100,
            "supplier_price_usd_per_ton": 1000,
            "delivery_date": "2026-12-10",
        }

        for product_type, expected_spec, excluded_spec in cases:
            with self.subTest(product_type=product_type):
                order["product_type"] = product_type
                contract = purchase_contract.render_contract_text(
                    order, "buyer"
                )

                self.assertIn(expected_spec, contract)
                self.assertNotIn(excluded_spec, contract)
                self.assertIn("2024/25 Crop", contract)
                self.assertNotIn("{?", contract)
                self.assertNotIn("{purity}", contract)


if __name__ == "__main__":
    unittest.main()
