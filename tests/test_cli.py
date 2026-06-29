from __future__ import annotations

import tempfile
import unittest
import os
import subprocess
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from cronpot.cli import _copy_local_vault_for_push, _github_push_script, _ingest_title, _k8s_vault_destination, main
from cronpot.config import AutomationConfig
from cronpot.jobs import enqueue_ingest_job, list_jobs
from cronpot.llm import IngredientAliasSuggestion
from cronpot.models import Recipe
from cronpot.vault import write_recipe_to_vault


class CliTests(unittest.TestCase):
    def test_html_command_writes_selected_recipe_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir)
            output = vault / "cookbook.html"
            write_recipe_to_vault(
                Recipe(
                    title="Aglio e Olio",
                    ingredients=["100g spaghetti"],
                    steps=["Boil the pasta."],
                    tags=["parev"],
                    categories=["Mains"],
                ),
                vault,
            )

            with redirect_stdout(StringIO()):
                exit_code = main(["html", "Aglio e Olio", "--vault", str(vault), "--output", str(output)])

            self.assertEqual(exit_code, 0)
            text = output.read_text(encoding="utf-8")
            self.assertIn("<h2>Aglio e Olio</h2>", text)
            self.assertIn("<li>100g spaghetti</li>", text)

    def test_export_command_defaults_to_html(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir)
            write_recipe_to_vault(
                Recipe(
                    title="Aglio e Olio",
                    ingredients=["100g spaghetti"],
                    steps=["Boil the pasta."],
                    tags=["parev"],
                    categories=["Mains"],
                ),
                vault,
            )

            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["export", "Aglio e Olio", "--vault", str(vault)])

            self.assertEqual(exit_code, 0)
            self.assertIn("<!doctype html>", stdout.getvalue())
            self.assertIn("<h2>Aglio e Olio</h2>", stdout.getvalue())

    def test_export_command_supports_markdown_format(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir)
            write_recipe_to_vault(
                Recipe(
                    title="Aglio e Olio",
                    ingredients=["100g spaghetti"],
                    steps=["Boil the pasta."],
                    tags=["parev"],
                    categories=["Mains"],
                ),
                vault,
            )

            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["export", "Aglio e Olio", "--vault", str(vault), "--format", "markdown"])

            self.assertEqual(exit_code, 0)
            self.assertIn("# Aglio e Olio", stdout.getvalue())
            self.assertIn("- 100g spaghetti", stdout.getvalue())

    def test_jobs_clear_command_removes_stored_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir)
            enqueue_ingest_job(vault, "https://example.com/soup")
            stdout = StringIO()

            with redirect_stdout(stdout):
                exit_code = main(["jobs", "clear", "--vault", str(vault)])

            self.assertEqual(exit_code, 0)
            self.assertEqual(list_jobs(vault), [])
            self.assertIn("Cleared 1 job.", stdout.getvalue())

    def test_export_command_supports_pdf_format(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir)
            output = vault / "cookbook.pdf"
            write_recipe_to_vault(
                Recipe(
                    title="Aglio e Olio",
                    ingredients=["100g spaghetti"],
                    steps=["Boil the pasta."],
                    tags=["parev"],
                    categories=["Mains"],
                ),
                vault,
            )

            with redirect_stdout(StringIO()):
                exit_code = main(["export", "Aglio e Olio", "--vault", str(vault), "--format", "pdf", "--output", str(output)])

            self.assertEqual(exit_code, 0)
            self.assertTrue(output.read_bytes().startswith(b"%PDF-1.4"))

    def test_export_pdf_requires_output_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir)
            workdir = Path(temp_dir) / "out"
            workdir.mkdir()
            write_recipe_to_vault(
                Recipe(
                    title="Aglio e Olio",
                    ingredients=["100g spaghetti"],
                    steps=["Boil the pasta."],
                    tags=["parev"],
                    categories=["Mains"],
                ),
                vault,
            )

            previous_cwd = Path.cwd()
            try:
                os.chdir(workdir)
                with redirect_stdout(StringIO()):
                    exit_code = main(["export", "Aglio e Olio", "--vault", str(vault), "--format", "pdf"])
            finally:
                os.chdir(previous_cwd)

            self.assertEqual(exit_code, 0)
            self.assertTrue((workdir / "Aglio e Olio.pdf").exists())

    def test_start_command_runs_server(self) -> None:
        with patch("cronpot.cli.run_server") as run_server, redirect_stdout(StringIO()):
            exit_code = main(["start", "--vault", "docs", "--host", "127.0.0.1", "--port", "9090"])

        self.assertEqual(exit_code, 0)
        run_server.assert_called_once()
        self.assertEqual(run_server.call_args.args[0], "127.0.0.1")
        self.assertEqual(run_server.call_args.args[1], 9090)
        self.assertEqual(run_server.call_args.args[2], "docs")
        self.assertEqual(run_server.call_args.kwargs["pairing_code"], "")

    def test_start_command_prints_default_port(self) -> None:
        stdout = StringIO()
        with patch("cronpot.cli.run_server"), redirect_stdout(stdout):
            exit_code = main(["start"])

        self.assertEqual(exit_code, 0)
        self.assertIn("CronPot serving on http://0.0.0.0:8080", stdout.getvalue())

    def test_start_lan_command_prints_pairing_code_and_mobile_url(self) -> None:
        stdout = StringIO()
        with patch("cronpot.cli.run_server") as run_server, patch("cronpot.cli._local_network_addresses", return_value=["192.168.1.10"]), redirect_stdout(stdout):
            exit_code = main(["start", "--lan", "--auth-code", "123456", "--vault", "docs"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(run_server.call_args.kwargs["pairing_code"], "123456")
        self.assertIn("CronPot mobile pairing code: 123456", stdout.getvalue())
        self.assertIn("http://192.168.1.10:8080/mobile", stdout.getvalue())

    def test_start_command_uses_auth_code_from_environment(self) -> None:
        with patch.dict(os.environ, {"CRONPOT_AUTH_CODE": "234567"}), patch("cronpot.cli.run_server") as run_server, redirect_stdout(StringIO()):
            exit_code = main(["start", "--vault", "docs"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(run_server.call_args.kwargs["pairing_code"], "234567")

    def test_k8s_github_secret_sets_author_fields(self) -> None:
        with patch.dict(os.environ, {"CRONPOT_GITHUB_TOKEN": "token"}), patch("cronpot.cli._run"), patch("cronpot.cli._run_with_input") as apply, redirect_stdout(StringIO()):
            exit_code = main(
                [
                    "k",
                    "github",
                    "secret",
                    "--namespace",
                    "cronpot-local",
                    "--repo",
                    "https://github.com/example/vault.git",
                    "--author-name",
                    "cronpot-bot",
                    "--author-email",
                    "cronpot-bot@example.local",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(apply.call_args.args[0], ["kubectl", "apply", "-f", "-"])
        secret_yaml = apply.call_args.args[1]
        self.assertIn("author_name: 'cronpot-bot'", secret_yaml)
        self.assertIn("author_email: 'cronpot-bot@example.local'", secret_yaml)

    def test_k8s_github_secret_rejects_credentials_in_repository_url(self) -> None:
        stderr = StringIO()
        with patch.dict(os.environ, {"CRONPOT_GITHUB_TOKEN": "token"}), redirect_stderr(stderr):
            exit_code = main(
                [
                    "k8s",
                    "github",
                    "secret",
                    "--repo",
                    "https://token@github.com/example/vault.git",
                ]
            )

        self.assertEqual(exit_code, 1)
        self.assertIn("must not contain credentials", stderr.getvalue())

    def test_k8s_github_secret_rejects_project_repository_by_default(self) -> None:
        with patch.dict(os.environ, {"CRONPOT_GITHUB_TOKEN": "token"}), patch(
            "cronpot.cli._git_remote_url", return_value="git@github.com:example/cronpot.git"
        ), redirect_stderr(stderr := StringIO()):
            exit_code = main(
                [
                    "k8s",
                    "github",
                    "secret",
                    "--repo",
                    "https://github.com/example/cronpot.git",
                ]
            )

        self.assertEqual(exit_code, 1)
        self.assertIn("matches this CronPot project repository", stderr.getvalue())

    def test_k8s_github_push_creates_sync_job(self) -> None:
        with patch("cronpot.cli._run_with_input") as apply, patch("cronpot.cli._run") as run, patch("cronpot.cli.subprocess.run"), patch("cronpot.cli.time.strftime", return_value="20260617010101"), redirect_stdout(StringIO()):
            exit_code = main(["k8s", "github", "push", "--namespace", "cronpot-local", "--message", "Sync from test", "--no-seed"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(apply.call_args.args[0], ["kubectl", "apply", "-f", "-"])
        yaml = apply.call_args.args[1]
        self.assertIn("name: cronpot-github-push-20260617010101", yaml)
        self.assertIn("value: 'Sync from test'", yaml)
        self.assertIn("git config --global --add safe.directory /work/repo", yaml)
        self.assertIn('if [ ! -d "$repo_path" ]; then', yaml)
        self.assertNotIn('rm -rf "$repo_path"', yaml)
        self.assertIn("key: author_name", yaml)
        self.assertIn("key: author_email", yaml)
        self.assertEqual(run.call_args_list[0].args[0][:4], ["kubectl", "-n", "cronpot-local", "wait"])

    def test_k8s_sync_back_copies_pvc_to_local_target(self) -> None:
        with patch("cronpot.cli._sync_k8s_vault_back") as sync_back:
            exit_code = main(["k8s", "sync-back", "docs", "--namespace", "cronpot-local", "--commit", "--message", "Sync test"])

        self.assertEqual(exit_code, 0)
        sync_back.assert_called_once_with("docs", "cronpot-local", True, "Sync test")

    def test_k8s_sync_back_uses_relative_kubectl_copy_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("cronpot.cli._running_api_pod", return_value="cronpot-api-123"), patch("cronpot.cli._run") as run, patch("cronpot.cli._copy_synced_vault", return_value=3), redirect_stdout(StringIO()):
            target = Path(temp_dir) / "docs"

            from cronpot.cli import _sync_k8s_vault_back

            _sync_k8s_vault_back(str(target), "cronpot-local", False, "Sync test")

        command = run.call_args.args[0]
        self.assertEqual(command, ["kubectl", "-n", "cronpot-local", "cp", "cronpot-api-123:/vault/.", "vault", "--container", "api"])
        self.assertNotIn(":", command[5])

    def test_k8s_sync_back_unwraps_matching_vault_folder(self) -> None:
        from cronpot.cli import _sync_back_source

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            staging = root / "staging"
            target = root / "docs"
            nested = staging / "docs"
            nested.mkdir(parents=True)
            target.mkdir()
            (nested / "Bolognese.md").write_text("recipe", encoding="utf-8")

            with redirect_stdout(StringIO()):
                source = _sync_back_source(staging, target)

            self.assertEqual(source, nested)

    def test_k8s_push_local_defaults_to_matching_vault_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("cronpot.cli._running_api_pod", return_value="cronpot-api-123"), patch("cronpot.cli._run") as run, patch(
            "cronpot.cli._kubectl_output",
            return_value="1",
        ):
            source = Path(temp_dir) / "docs"
            source.mkdir()
            (source / "Soup.md").write_text("# Soup", encoding="utf-8")
            stdout = StringIO()

            with redirect_stdout(stdout):
                exit_code = main(["k8s", "push-local", str(source), "--namespace", "cronpot-local"])

        self.assertEqual(exit_code, 0)
        run.assert_any_call(["kubectl", "-n", "cronpot-local", "exec", "cronpot-api-123", "--", "mkdir", "-p", "/vault/docs"])
        copy_commands = [call.args[0] for call in run.call_args_list if call.args[0][:4] == ["kubectl", "-n", "cronpot-local", "cp"]]
        self.assertEqual(copy_commands[0][4], "docs/.")
        self.assertEqual(copy_commands[0][5], "cronpot-api-123:/vault/docs")
        self.assertIn("/vault/docs", stdout.getvalue())

    def test_copy_local_vault_for_push_skips_runtime_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "docs"
            target = root / "staged"
            (source / ".cronpot" / "jobs").mkdir(parents=True)
            target.mkdir()
            (source / ".cronpot" / "jobs" / "job.json").write_text("{}", encoding="utf-8")
            (source / "Soup.md").write_text("# Soup", encoding="utf-8")

            _copy_local_vault_for_push(source, target)

            self.assertTrue((target / "Soup.md").exists())
            self.assertFalse((target / ".cronpot").exists())

    def test_k8s_push_local_can_clear_destination_first(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("cronpot.cli._running_api_pod", return_value="cronpot-api-123"), patch("cronpot.cli._run") as run, patch(
            "cronpot.cli._kubectl_output",
            return_value="1",
        ):
            source = Path(temp_dir) / "recipes"
            source.mkdir()
            with redirect_stdout(StringIO()):
                main(["k8s", "push-local", str(source), "--namespace", "cronpot-local", "--destination", "/vault/docs", "--clear"])

        clear_commands = [call.args[0] for call in run.call_args_list if "find '/vault/docs'" in " ".join(call.args[0])]
        self.assertEqual(len(clear_commands), 1)

    def test_k8s_vault_destination_rejects_paths_outside_vault(self) -> None:
        with self.assertRaisesRegex(ValueError, "below /vault"):
            _k8s_vault_destination(Path("docs"), "/tmp/docs")

    def test_k8s_github_pull_can_sync_back_after_pull(self) -> None:
        with patch("cronpot.cli._run_github_sync_job") as run_job, patch("cronpot.cli._print_k8s_vault_summary") as summary, patch("cronpot.cli._sync_k8s_vault_back") as sync_back:
            exit_code = main(["k8s", "github", "pull", "--namespace", "cronpot-local", "--sync-back", "docs", "--commit-sync-back"])

        self.assertEqual(exit_code, 0)
        run_job.assert_called_once_with("pull", "cronpot-local", "Sync CronPot vault from GitHub", 180, False)
        summary.assert_called_once_with("cronpot-local")
        sync_back.assert_called_once_with("docs", "cronpot-local", True, "Sync CronPot vault from Kubernetes")

    def test_k8s_github_push_seeds_from_local_vault_by_default(self) -> None:
        with patch("cronpot.cli._seed_local_vault_for_github_push") as seed, patch("cronpot.cli._run_github_sync_job") as run_job:
            exit_code = main(["k8s", "github", "push", "--namespace", "cronpot-local"])

        self.assertEqual(exit_code, 0)
        seed.assert_called_once_with("docs", "cronpot-local")
        run_job.assert_called_once_with("push", "cronpot-local", "Sync CronPot vault from Kubernetes", 180, False)

    def test_k8s_github_push_can_skip_local_seed(self) -> None:
        with patch("cronpot.cli._seed_local_vault_for_github_push") as seed, patch("cronpot.cli._run_github_sync_job") as run_job:
            exit_code = main(["k8s", "github", "push", "--namespace", "cronpot-local", "--no-seed"])

        self.assertEqual(exit_code, 0)
        seed.assert_not_called()
        run_job.assert_called_once_with("push", "cronpot-local", "Sync CronPot vault from Kubernetes", 180, False)

    def test_k8s_github_seed_preserves_docs_folder_layout(self) -> None:
        with patch("cronpot.cli._push_local_vault_to_k8s") as push_local, patch("cronpot.cli._remove_k8s_duplicate_root_markdown") as cleanup:
            from cronpot.cli import _seed_local_vault_for_github_push

            _seed_local_vault_for_github_push("docs", "cronpot-local")

        push_local.assert_called_once_with("docs", "cronpot-local", None, True)
        cleanup.assert_called_once_with("cronpot-local", "docs")

    def test_github_push_script_removes_root_markdown_duplicates(self) -> None:
        script = _github_push_script()

        self.assertIn('if [ -d "$repo_path/docs" ]; then', script)
        self.assertIn('[ -e "$repo_path/docs/$name" ]', script)
        self.assertIn('rm -f "$file"', script)
        self.assertIn('[ "$name" = "README.md" ] && continue', script)

    def test_k8s_status_prints_cluster_summary(self) -> None:
        responses = [
            subprocess.CompletedProcess(["kubectl"], 0, "Client Version: v1.30", ""),
            subprocess.CompletedProcess(["kubectl"], 0, "Kubernetes control plane is running", ""),
            subprocess.CompletedProcess(["kubectl"], 0, "namespace/cronpot-local", ""),
            subprocess.CompletedProcess(["kubectl"], 0, "cronpot-api-123", ""),
            subprocess.CompletedProcess(["kubectl"], 0, "1", ""),
            subprocess.CompletedProcess(["kubectl"], 0, "120", ""),
            subprocess.CompletedProcess(["kubectl"], 0, "3", ""),
        ]
        stdout = StringIO()
        with patch("cronpot.cli.subprocess.run", side_effect=responses), redirect_stdout(stdout):
            exit_code = main(["k8s", "status", "--namespace", "cronpot-local"])

        self.assertEqual(exit_code, 0)
        self.assertIn("cluster: reachable", stdout.getvalue())
        self.assertIn("api pod: cronpot-api-123", stdout.getvalue())
        self.assertIn("vault recipes: 120", stdout.getvalue())

    def test_normalise_ingredients_suggest_prints_aliases(self) -> None:
        stdout = StringIO()
        with patch(
            "cronpot.cli.suggest_ingredient_aliases",
            return_value=[IngredientAliasSuggestion("granulated sugar", "sugar", 3)],
        ) as suggest, redirect_stdout(stdout):
            exit_code = main(["normalise", "ingredients", "--vault", "docs", "--suggest", "--model", "qwen2.5:3b"])

        self.assertEqual(exit_code, 0)
        self.assertIn("granulated sugar -> sugar (3)", stdout.getvalue())
        self.assertEqual(suggest.call_args.args[1].llm_model, "qwen2.5:3b")

    def test_analytics_uses_configured_llm_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir)
            write_recipe_to_vault(
                Recipe(
                    title="Cake",
                    ingredients=["100g extra fine sugar", "50g sugar"],
                    steps=["Bake."],
                    tags=["dessert", "parev"],
                    categories=["Desserts"],
                ),
                vault,
            )

            stdout = StringIO()
            with patch("cronpot.cli.load_config") as load_config, patch(
                "cronpot.cli.suggest_ingredient_alias_map",
                return_value={"extra fine sugar": "sugar"},
            ):
                load_config.return_value.default_vault = str(vault)
                load_config.return_value.llm_auto_normalise_ingredients = True
                load_config.return_value.llm_ingredient_limit = 120
                with redirect_stdout(stdout):
                    exit_code = main(["analytics", "--vault", str(vault)])

            self.assertEqual(exit_code, 0)
            self.assertIn("- sugar: 2", stdout.getvalue())

    def test_ingest_rewrites_recipe_when_configured(self) -> None:
        html = """
        <script type="application/ld+json">
        {"@type":"Recipe","name":"messy soup","recipeIngredient":["one carrot"],"recipeInstructions":["you should chop it"]}
        </script>
        """
        rewritten = Recipe(
            title="Carrot Soup",
            ingredients=["1 carrot"],
            steps=["Chop the carrot."],
            tags=["starter", "parev"],
            categories=["Soups"],
            source="https://example.com/soup",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir)
            stdout = StringIO()
            with patch("cronpot.cli.load_config", return_value=AutomationConfig(llm_rewrite_ingested_recipes=True)), patch(
                "cronpot.cli.fetch_html",
                return_value=html,
            ), patch("cronpot.ingest.rewrite_recipe_to_vault_style", return_value=rewritten) as rewrite, redirect_stdout(stdout):
                exit_code = main(["ingest", "https://example.com/soup", "--vault", str(vault), "--dry-run"])

            self.assertEqual(exit_code, 0)
            self.assertIn("#", stdout.getvalue())
            self.assertIn("- 1 carrot", stdout.getvalue())
            self.assertIn("1. Chop the carrot.", stdout.getvalue())
            rewrite.assert_called_once()

    def test_ingest_title_accepts_extracted_suggestion_when_prompt_response_is_empty(self) -> None:
        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value=""):
            title = _ingest_title("Carrot Soup", None)

        self.assertEqual(title, "Carrot Soup")

    def test_ingest_title_uses_prompt_response(self) -> None:
        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="Friday Night Soup"):
            title = _ingest_title("Carrot Soup", None)

        self.assertEqual(title, "Friday Night Soup")

    def test_ingest_title_uses_cli_override_without_prompting(self) -> None:
        with patch("builtins.input") as prompt:
            title = _ingest_title("Carrot Soup", "Shabbat Soup")

        self.assertEqual(title, "Shabbat Soup")
        prompt.assert_not_called()

    def test_ingest_command_title_override_changes_written_file(self) -> None:
        html = """
        <script type="application/ld+json">
        {"@type":"Recipe","name":"messy soup","recipeIngredient":["one carrot"],"recipeInstructions":["chop it"]}
        </script>
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir)
            with patch("cronpot.cli.load_config", return_value=AutomationConfig()), patch("cronpot.cli.fetch_html", return_value=html), redirect_stdout(StringIO()):
                exit_code = main(["ingest", "https://example.com/soup", "--vault", str(vault), "--title", "Carrot Soup"])

            self.assertEqual(exit_code, 0)
            self.assertTrue((vault / "Carrot Soup.md").exists())


if __name__ == "__main__":
    unittest.main()
