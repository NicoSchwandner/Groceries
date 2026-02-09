"""Tests for quantity parsing and combination logic in weekly.py."""

import unittest

from weekly import (
    _best_volume_unit,
    _best_weight_unit,
    combine_quantities,
    parse_ingredient_line,
)


class TestBestVolumeUnit(unittest.TestCase):
    """Tests for _best_volume_unit: picks the best display unit for a volume in ml."""

    def test_small_values_stay_in_ml(self):
        self.assertEqual(_best_volume_unit(50), (50, "ml"))

    def test_exactly_100ml_becomes_dl(self):
        self.assertEqual(_best_volume_unit(100), (1.0, "dl"))

    def test_250ml_becomes_dl(self):
        val, unit = _best_volume_unit(250)
        self.assertEqual(unit, "dl")
        self.assertAlmostEqual(val, 2.5)

    def test_1000ml_becomes_l(self):
        self.assertEqual(_best_volume_unit(1000), (1.0, "l"))

    def test_2000ml_becomes_l(self):
        self.assertEqual(_best_volume_unit(2000), (2.0, "l"))

    def test_1500ml_stays_dl_because_not_divisible_by_1000(self):
        # 1500 % 1000 != 0, so it falls to dl
        val, unit = _best_volume_unit(1500)
        self.assertEqual(unit, "dl")
        self.assertAlmostEqual(val, 15.0)

    def test_99ml_stays_in_ml(self):
        self.assertEqual(_best_volume_unit(99), (99, "ml"))

    def test_zero_ml(self):
        # Zero is < 100, so stays in ml
        self.assertEqual(_best_volume_unit(0), (0, "ml"))

    def test_1ml(self):
        self.assertEqual(_best_volume_unit(1), (1, "ml"))

    def test_3000ml_becomes_l(self):
        self.assertEqual(_best_volume_unit(3000), (3.0, "l"))


class TestBestWeightUnit(unittest.TestCase):
    """Tests for _best_weight_unit: picks the best display unit for a weight in grams."""

    def test_small_values_stay_in_g(self):
        self.assertEqual(_best_weight_unit(500), (500, "g"))

    def test_exactly_1000g_becomes_kg(self):
        self.assertEqual(_best_weight_unit(1000), (1.0, "kg"))

    def test_1500g_becomes_kg(self):
        val, unit = _best_weight_unit(1500)
        self.assertEqual(unit, "kg")
        self.assertAlmostEqual(val, 1.5)

    def test_999g_stays_in_g(self):
        self.assertEqual(_best_weight_unit(999), (999, "g"))

    def test_zero_g(self):
        self.assertEqual(_best_weight_unit(0), (0, "g"))

    def test_2500g_becomes_kg(self):
        val, unit = _best_weight_unit(2500)
        self.assertEqual(unit, "kg")
        self.assertAlmostEqual(val, 2.5)


class TestCombineQuantities(unittest.TestCase):
    """Tests for combine_quantities: sums values with unit conversion."""

    # --- Empty / trivial ---

    def test_empty_list(self):
        self.assertEqual(combine_quantities([]), "")

    def test_single_item_no_unit(self):
        result = combine_quantities([("2", "")])
        self.assertEqual(result, "2")

    def test_single_item_with_unit(self):
        result = combine_quantities([("3", "msk")])
        self.assertEqual(result, "3 msk")

    # --- Volume conversion ---

    def test_ml_addition(self):
        result = combine_quantities([("100", "ml"), ("200", "ml")])
        self.assertEqual(result, "3 dl")

    def test_dl_addition(self):
        result = combine_quantities([("2", "dl"), ("3", "dl")])
        self.assertEqual(result, "5 dl")

    def test_mixed_ml_and_dl(self):
        result = combine_quantities([("50", "ml"), ("1", "dl")])
        # 50 + 100 = 150 ml = 1.5 dl
        self.assertEqual(result, "1.5 dl")

    def test_cl_to_dl(self):
        result = combine_quantities([("10", "cl"), ("10", "cl")])
        # 100 + 100 = 200 ml = 2 dl
        self.assertEqual(result, "2 dl")

    def test_dl_to_l_exact(self):
        result = combine_quantities([("5", "dl"), ("5", "dl")])
        # 500 + 500 = 1000 ml -> 1 l (exactly divisible by 1000)
        self.assertEqual(result, "1 l")

    def test_large_volume_not_exact_liters(self):
        result = combine_quantities([("5", "dl"), ("3", "dl")])
        # 500 + 300 = 800 ml -> 8 dl (not divisible by 1000)
        self.assertEqual(result, "8 dl")

    def test_mixed_cl_and_l(self):
        result = combine_quantities([("50", "cl"), ("1", "l")])
        # 500 + 1000 = 1500 ml -> 15 dl (not exactly divisible by 1000)
        self.assertEqual(result, "15 dl")

    # --- Weight conversion ---

    def test_g_addition(self):
        result = combine_quantities([("200", "g"), ("300", "g")])
        self.assertEqual(result, "500 g")

    def test_g_to_kg(self):
        result = combine_quantities([("500", "g"), ("600", "g")])
        # 1100 g -> 1.1 kg
        self.assertEqual(result, "1.1 kg")

    def test_mixed_g_and_kg(self):
        result = combine_quantities([("500", "g"), ("1", "kg")])
        # 500 + 1000 = 1500 g -> 1.5 kg
        self.assertEqual(result, "1.5 kg")

    def test_kg_addition(self):
        result = combine_quantities([("1", "kg"), ("1", "kg")])
        self.assertEqual(result, "2 kg")

    # --- Other units (msk, tsk, st, etc.) ---

    def test_msk_addition(self):
        result = combine_quantities([("2", "msk"), ("3", "msk")])
        self.assertEqual(result, "5 msk")

    def test_tsk_addition(self):
        result = combine_quantities([("1", "tsk"), ("0.5", "tsk")])
        self.assertEqual(result, "1.5 tsk")

    def test_st_addition(self):
        result = combine_quantities([("2", "st"), ("4", "st")])
        self.assertEqual(result, "6 st")

    # --- Mixed categories ---

    def test_volume_and_weight_together(self):
        result = combine_quantities([("2", "dl"), ("200", "g")])
        self.assertEqual(result, "2 dl + 200 g")

    def test_volume_weight_and_other(self):
        result = combine_quantities([("2", "dl"), ("200", "g"), ("1", "msk")])
        self.assertEqual(result, "2 dl + 200 g + 1 msk")

    # --- Unparseable values (ranges, etc.) ---

    def test_range_is_unparseable(self):
        result = combine_quantities([("1-2", "st")])
        self.assertEqual(result, "1-2 st")

    def test_range_with_en_dash(self):
        result = combine_quantities([("1\u20132", "st")])
        self.assertEqual(result, "1\u20132 st")

    def test_parseable_and_unparseable_mixed(self):
        result = combine_quantities([("2", "dl"), ("1-2", "st")])
        self.assertEqual(result, "2 dl + 1-2 st")

    def test_multiple_unparseable(self):
        result = combine_quantities([("1-2", "st"), ("3-4", "msk")])
        self.assertEqual(result, "1-2 st + 3-4 msk")

    # --- Comma as decimal separator ---

    def test_comma_decimal(self):
        result = combine_quantities([("1,5", "dl")])
        self.assertEqual(result, "1.5 dl")

    def test_comma_decimal_addition(self):
        result = combine_quantities([("1,5", "dl"), ("2,5", "dl")])
        # 150 + 250 = 400 ml = 4 dl
        self.assertEqual(result, "4 dl")

    # --- Display formatting (trailing zeros stripped) ---

    def test_trailing_zeros_stripped(self):
        result = combine_quantities([("1", "kg")])
        # Should be "1 kg" not "1.0 kg"
        self.assertEqual(result, "1 kg")

    def test_no_unit_addition(self):
        result = combine_quantities([("2", ""), ("3", "")])
        self.assertEqual(result, "5")

    # --- Case insensitivity for known units ---

    def test_uppercase_ml(self):
        result = combine_quantities([("100", "ML"), ("100", "ml")])
        self.assertEqual(result, "2 dl")

    def test_uppercase_g(self):
        result = combine_quantities([("500", "G"), ("500", "G")])
        self.assertEqual(result, "1 kg")


class TestParseIngredientLine(unittest.TestCase):
    """Tests for parse_ingredient_line: parses raw ingredient strings."""

    # --- Basic parsing ---

    def test_simple_qty_unit_name(self):
        result = parse_ingredient_line("2 dl mjölk")
        self.assertEqual(result["qty"], "2")
        self.assertEqual(result["unit"], "dl")
        self.assertEqual(result["name"], "mjölk")

    def test_qty_no_unit(self):
        result = parse_ingredient_line("2 ägg")
        self.assertEqual(result["qty"], "2")
        self.assertEqual(result["unit"], "")
        self.assertEqual(result["name"], "ägg")

    def test_no_qty_no_unit(self):
        result = parse_ingredient_line("salt and pepper")
        self.assertEqual(result["qty"], "")
        self.assertEqual(result["unit"], "")
        self.assertEqual(result["name"], "salt and pepper")

    # --- Decimal quantities ---

    def test_decimal_qty(self):
        result = parse_ingredient_line("1.5 dl grädde")
        self.assertEqual(result["qty"], "1.5")
        self.assertEqual(result["unit"], "dl")
        self.assertEqual(result["name"], "grädde")

    def test_comma_decimal_qty(self):
        result = parse_ingredient_line("1,5 dl grädde")
        self.assertEqual(result["qty"], "1,5")
        self.assertEqual(result["unit"], "dl")

    # --- No-space format (400g flour) ---

    def test_no_space_between_qty_and_unit(self):
        result = parse_ingredient_line("400g flour")
        self.assertEqual(result["qty"], "400")
        self.assertEqual(result["unit"], "g")
        self.assertEqual(result["name"], "flour")

    def test_no_space_ml(self):
        result = parse_ingredient_line("200ml coconut milk")
        self.assertEqual(result["qty"], "200")
        self.assertEqual(result["unit"], "ml")
        self.assertEqual(result["name"], "coconut milk")

    def test_no_space_kg(self):
        result = parse_ingredient_line("1.5kg potatis")
        self.assertEqual(result["qty"], "1.5")
        self.assertEqual(result["unit"], "kg")
        self.assertEqual(result["name"], "potatis")

    # --- English unit normalization ---

    def test_tablespoon_to_msk(self):
        result = parse_ingredient_line("2 tablespoons olive oil")
        self.assertEqual(result["unit"], "msk")
        self.assertEqual(result["name"], "olive oil")

    def test_teaspoon_to_tsk(self):
        result = parse_ingredient_line("1 teaspoon salt")
        self.assertEqual(result["unit"], "tsk")

    def test_tbsp_to_msk(self):
        result = parse_ingredient_line("1 tbsp soy sauce")
        self.assertEqual(result["unit"], "msk")

    def test_tsp_to_tsk(self):
        result = parse_ingredient_line("0.5 tsp cumin")
        self.assertEqual(result["unit"], "tsk")

    def test_cup_to_dl(self):
        result = parse_ingredient_line("1 cup rice")
        self.assertEqual(result["unit"], "dl")

    def test_clove_singular_to_klyfta(self):
        result = parse_ingredient_line("1 clove garlic")
        self.assertEqual(result["unit"], "klyfta")

    def test_cloves_plural_to_klyftor(self):
        result = parse_ingredient_line("3 cloves garlic")
        self.assertEqual(result["unit"], "klyftor")

    # --- Parenthetical notes stripped ---

    def test_strips_parenthetical_notes(self):
        result = parse_ingredient_line("400g chicken breast (diced)")
        self.assertEqual(result["name"], "chicken breast")

    def test_strips_long_parenthetical(self):
        result = parse_ingredient_line("2 dl cream (I use oat cream)")
        self.assertEqual(result["name"], "cream")

    # --- Trailing prep notes stripped ---

    def test_strips_chopped(self):
        result = parse_ingredient_line("1 st onion, chopped")
        self.assertEqual(result["name"], "onion")

    def test_strips_finely_diced(self):
        result = parse_ingredient_line("2 st carrots, finely diced")
        self.assertEqual(result["name"], "carrots")

    def test_strips_grated(self):
        result = parse_ingredient_line("100g cheese, grated")
        self.assertEqual(result["name"], "cheese")

    def test_strips_minced(self):
        result = parse_ingredient_line("3 cloves garlic, minced")
        self.assertEqual(result["name"], "garlic")

    def test_strips_sliced(self):
        result = parse_ingredient_line("1 st cucumber, sliced")
        self.assertEqual(result["name"], "cucumber")

    def test_strips_roughly_chopped(self):
        result = parse_ingredient_line("200g tomatoes, roughly chopped")
        self.assertEqual(result["name"], "tomatoes")

    # --- Unicode fractions ---

    def test_unicode_half(self):
        result = parse_ingredient_line("\u00bd dl cream")
        self.assertEqual(result["qty"], "0.5")
        self.assertEqual(result["unit"], "dl")

    def test_unicode_quarter(self):
        result = parse_ingredient_line("\u00bc tsp salt")
        self.assertEqual(result["qty"], "0.25")
        self.assertEqual(result["unit"], "tsk")

    def test_unicode_three_quarter(self):
        result = parse_ingredient_line("\u00be dl milk")
        self.assertEqual(result["qty"], "0.75")
        self.assertEqual(result["unit"], "dl")

    def test_unicode_one_third(self):
        result = parse_ingredient_line("\u2153 dl water")
        self.assertEqual(result["qty"], "0.33")
        self.assertEqual(result["unit"], "dl")

    def test_unicode_two_thirds(self):
        result = parse_ingredient_line("\u2154 dl stock")
        self.assertEqual(result["qty"], "0.67")
        self.assertEqual(result["unit"], "dl")

    # --- Range quantities ---

    def test_range_with_hyphen(self):
        result = parse_ingredient_line("1-2 dl water")
        self.assertEqual(result["qty"], "1-2")
        self.assertEqual(result["unit"], "dl")
        self.assertEqual(result["name"], "water")

    def test_range_with_en_dash(self):
        result = parse_ingredient_line("1\u20132 st tomatoes")
        self.assertEqual(result["qty"], "1\u20132")
        self.assertEqual(result["unit"], "st")

    # --- raw field preserved ---

    def test_raw_preserved(self):
        raw = "  2 dl mjölk  "
        result = parse_ingredient_line(raw)
        self.assertEqual(result["raw"], raw)

    # --- Whitespace handling ---

    def test_leading_trailing_whitespace(self):
        result = parse_ingredient_line("   3 msk socker   ")
        self.assertEqual(result["qty"], "3")
        self.assertEqual(result["unit"], "msk")
        self.assertEqual(result["name"], "socker")

    def test_empty_string(self):
        result = parse_ingredient_line("")
        self.assertEqual(result["qty"], "")
        self.assertEqual(result["unit"], "")
        self.assertEqual(result["name"], "")

    def test_whitespace_only(self):
        result = parse_ingredient_line("   ")
        self.assertEqual(result["qty"], "")
        self.assertEqual(result["unit"], "")
        self.assertEqual(result["name"], "")

    # --- Swedish units ---

    def test_krm(self):
        result = parse_ingredient_line("2 krm salt")
        self.assertEqual(result["qty"], "2")
        self.assertEqual(result["unit"], "krm")
        self.assertEqual(result["name"], "salt")

    def test_st(self):
        result = parse_ingredient_line("4 st morötter")
        self.assertEqual(result["qty"], "4")
        self.assertEqual(result["unit"], "st")
        self.assertEqual(result["name"], "morötter")

    def test_port(self):
        result = parse_ingredient_line("4 port ris")
        self.assertEqual(result["qty"], "4")
        self.assertEqual(result["unit"], "port")

    # --- Name-only lines (no quantity) ---

    def test_name_only_strips_parenthetical(self):
        result = parse_ingredient_line("fresh basil (optional)")
        self.assertEqual(result["name"], "fresh basil")
        self.assertEqual(result["qty"], "")

    def test_name_only_strips_prep(self):
        result = parse_ingredient_line("parsley, chopped")
        self.assertEqual(result["name"], "parsley")
        self.assertEqual(result["qty"], "")

    # --- packet/package normalization ---

    def test_packet_to_forpackning(self):
        result = parse_ingredient_line("1 packet yeast")
        self.assertEqual(result["unit"], "förp")

    def test_package_singular_to_forpackning(self):
        result = parse_ingredient_line("1 package noodles")
        self.assertEqual(result["unit"], "förp")

    def test_packages_plural_not_in_unit_map(self):
        # "packages" matches regex but is not in UNIT_MAP, so stays as-is
        result = parse_ingredient_line("2 packages noodles")
        self.assertEqual(result["unit"], "packages")

    # --- can/block ---

    def test_can_to_burk(self):
        result = parse_ingredient_line("1 can tomatoes")
        self.assertEqual(result["unit"], "burk")

    def test_block_unchanged(self):
        result = parse_ingredient_line("1 block tofu")
        self.assertEqual(result["unit"], "block")

    # --- bunch/pinch ---

    def test_bunch_to_bunt(self):
        result = parse_ingredient_line("1 bunch parsley")
        self.assertEqual(result["unit"], "bunt")

    def test_pinch_not_in_regex_so_no_unit(self):
        # "pinch" is in UNIT_MAP but not in ALL_UNITS regex, so it won't be
        # recognized as a unit -- the qty captures "1" and the rest is the name
        result = parse_ingredient_line("1 pinch salt")
        self.assertEqual(result["qty"], "1")
        self.assertEqual(result["unit"], "")
        self.assertEqual(result["name"], "pinch salt")

    def test_nypa_is_recognized(self):
        # "nypa" IS in ALL_UNITS (Swedish side) and is used directly
        result = parse_ingredient_line("1 nypa salt")
        self.assertEqual(result["unit"], "nypa")
        self.assertEqual(result["name"], "salt")

    # --- Fraction quantities ---

    def test_fraction_qty(self):
        # The regex allows / in qty
        result = parse_ingredient_line("1/2 dl cream")
        self.assertEqual(result["qty"], "1/2")
        self.assertEqual(result["unit"], "dl")


class TestCombineQuantitiesEdgeCases(unittest.TestCase):
    """Edge cases for combine_quantities."""

    def test_single_zero_qty(self):
        # 0 dl -> total_ml = 0, so no volume part is produced
        result = combine_quantities([("0", "dl")])
        self.assertEqual(result, "")

    def test_zero_and_nonzero(self):
        result = combine_quantities([("0", "dl"), ("2", "dl")])
        self.assertEqual(result, "2 dl")

    def test_many_small_ml_to_l(self):
        items = [("100", "ml")] * 10
        # 1000 ml -> 1 l
        result = combine_quantities(items)
        self.assertEqual(result, "1 l")

    def test_many_g_to_kg(self):
        items = [("250", "g")] * 4
        # 1000 g -> 1 kg
        result = combine_quantities(items)
        self.assertEqual(result, "1 kg")

    def test_unit_empty_string(self):
        result = combine_quantities([("5", "")])
        self.assertEqual(result, "5")

    def test_multiple_different_other_units_sorted(self):
        result = combine_quantities([("1", "msk"), ("2", "st"), ("3", "tsk")])
        # other_totals sorted by unit: msk, st, tsk
        self.assertEqual(result, "1 msk + 2 st + 3 tsk")

    def test_all_unparseable(self):
        result = combine_quantities([("1-2", "dl"), ("3-4", "msk")])
        self.assertEqual(result, "1-2 dl + 3-4 msk")

    def test_float_precision_display(self):
        # 1.5 dl + 1.5 dl = 300 ml = 3 dl
        result = combine_quantities([("1.5", "dl"), ("1.5", "dl")])
        self.assertEqual(result, "3 dl")

    def test_very_small_ml(self):
        result = combine_quantities([("5", "ml")])
        self.assertEqual(result, "5 ml")

    def test_cl_addition(self):
        result = combine_quantities([("5", "cl"), ("5", "cl")])
        # 50 + 50 = 100 ml = 1 dl
        self.assertEqual(result, "1 dl")


if __name__ == "__main__":
    unittest.main()
