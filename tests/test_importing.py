from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cronpot.importing import import_markdown_vault
from cronpot.vault import parse_markdown_recipe


class ImportingTests(unittest.TestCase):
    def test_imports_markdown_vault_into_target_schema(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            source = Path(source_dir)
            target = Path(target_dir)
            (source / "Chicken.md").write_text(
                """---
tags:
  - main
---
[[Mains]]

## Ingredients
- 1 chicken breast

## Method
1. Cook in a skillet.
""",
                encoding="utf-8",
            )

            result = import_markdown_vault(source, target)

            self.assertEqual(len(result.imported), 1)
            parsed = parse_markdown_recipe(result.imported[0])
            self.assertEqual(parsed.ingredients, ["1 chicken breast"])
            self.assertEqual(parsed.steps, ["Cook in a frying pan."])
            self.assertIn("meaty", parsed.tags)
            self.assertTrue(parsed.source_hash)

    def test_skips_incomplete_markdown_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            source = Path(source_dir)
            (source / "Index.md").write_text("# Index\n", encoding="utf-8")

            result = import_markdown_vault(source, target_dir)

            self.assertEqual(result.imported, [])
            self.assertEqual(len(result.skipped), 1)


if __name__ == "__main__":
    unittest.main()
