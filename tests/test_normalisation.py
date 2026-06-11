from __future__ import annotations

import unittest

from cronpot.config import AutomationConfig
from cronpot.models import Recipe
from cronpot.normalisation import infer_dietary_tags, normalise_recipe, normalise_text


class NormalisationTests(unittest.TestCase):
    def test_converts_common_us_terms_to_british_english(self) -> None:
        self.assertEqual(normalise_text("Broil scallions with cilantro in a skillet."), "Grill spring onions with coriander in a frying pan.")

    def test_infers_categories_and_dietary_tags(self) -> None:
        recipe = Recipe(
            title="Chicken Pasta",
            ingredients=["1 chicken breast", "200g spaghetti"],
            steps=["Cook everything."],
        )

        normalised = normalise_recipe(recipe)

        self.assertIn("Mains", [category.title() for category in normalised.categories])
        self.assertIn("main", normalised.tags)
        self.assertIn("meaty", normalised.tags)

    def test_marks_non_meat_non_dairy_recipes_as_parev(self) -> None:
        self.assertEqual(infer_dietary_tags("salmon with herbs and olive oil"), ["parev"])

    def test_meaty_takes_precedence_when_dietary_markers_conflict(self) -> None:
        self.assertEqual(infer_dietary_tags("chicken with cream"), ["meaty"])

    def test_can_disable_dietary_tag_enforcement_in_config(self) -> None:
        recipe = Recipe(title="Herbs", ingredients=["parsley"], steps=["Chop."])

        normalised = normalise_recipe(recipe, AutomationConfig(require_dietary_tag=False))

        self.assertNotIn("parev", normalised.tags)

    def test_converts_ascii_fractions_to_unicode_by_default(self) -> None:
        self.assertEqual(normalise_text("Add 1/2 tsp salt and 1 1/4 cups water."), "Add ½ tsp salt and 1¼ cups water.")

    def test_can_preserve_source_english_and_ascii_fractions(self) -> None:
        config = AutomationConfig(english="source", fraction_style="ascii")

        self.assertEqual(normalise_text("Broil 1/2 cup scallions.", config), "Broil 1/2 cup scallions.")


if __name__ == "__main__":
    unittest.main()
