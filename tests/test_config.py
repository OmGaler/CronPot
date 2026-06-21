from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cronpot.config import load_config


class ConfigTests(unittest.TestCase):
    def test_loads_default_schema_when_config_omits_schema_section(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "cronpot.toml"
            config_path.write_text(
                """
[recipe]
default_vault = "docs"

[llm]
rewrite_ingested_recipes = true
""",
                encoding="utf-8",
            )

            config = load_config(config_path)

        self.assertEqual(config.frontmatter_fields[0], "tags")
        self.assertIn("source_hash", config.frontmatter_fields)

    def test_loads_schema_style_and_worker_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "cronpot.toml"
            config_path.write_text(
                """
[schema]
ingredient_heading = "You Need"
method_heading = "You Do"
frontmatter_fields = ["tags", "source", "servings"]

[style]
english = "source"
fraction_style = "decimal"
method_style = "narrative"

[worker]
count = 4
""",
                encoding="utf-8",
            )

            config = load_config(config_path)

        self.assertEqual(config.ingredient_heading, "You Need")
        self.assertEqual(config.method_heading, "You Do")
        self.assertEqual(config.frontmatter_fields, ("tags", "source", "servings"))
        self.assertEqual(config.english, "source")
        self.assertEqual(config.fraction_style, "decimal")
        self.assertEqual(config.method_style, "narrative")
        self.assertEqual(config.worker_count, 4)


if __name__ == "__main__":
    unittest.main()
