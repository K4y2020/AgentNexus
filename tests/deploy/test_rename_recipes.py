"""Offline deployment/packaging regressions that require only the standard library."""

from __future__ import annotations

import ast
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_definitions(path: Path, names: set[str]) -> dict:
    """Load pure helpers without executing deployment/bootstrap imports."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tree.body = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in names
        or isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id in names for target in node.targets)
    ]
    namespace = {"re": re, "Path": Path}
    exec(compile(tree, str(path), "exec"), namespace)
    return namespace


class RenameRecipeTests(unittest.TestCase):
    def test_databricks_stamps_all_current_lockstep_pins(self) -> None:
        helper = load_definitions(
            ROOT / "deploy/databricks/deploy.py", {"set_version_in_pyproject"}
        )["set_version_in_pyproject"]
        original = (
            '[project]\nversion = "0.1.0"\ndependencies = [\n'
            '"agentnexus==0.1.0",\n"agentnexus-client==0.1.0",\n'
            '"agentnexus-ui-sdk==0.1.0",\n"unrelated==0.1.0"\n]\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pyproject.toml"
            path.write_text(original, encoding="utf-8")
            self.assertEqual(helper(path, "0.1.0.post123"), original)
            result = path.read_text(encoding="utf-8")
        for name in ("agentnexus", "agentnexus-client", "agentnexus-ui-sdk"):
            self.assertIn(f'"{name}==0.1.0.post123"', result)
        self.assertIn('"unrelated==0.1.0"', result)

    def test_homebrew_generator_consumes_renamed_template(self) -> None:
        directory = ROOT / ".github/scripts/homebrew"
        helpers = load_definitions(
            directory / "generate_formula.py", {"render_template", "_PLACEHOLDERS"}
        )
        formula = helpers["render_template"](
            (directory / "agentnexus.rb.template").read_text(encoding="utf-8"),
            "https://example.test/agentnexus.tar.gz",
            "a" * 64,
            "# test resources",
        )
        self.assertIn("class Agentnexus < Formula", formula)
        self.assertIn('libexec/"bin/agentnexus"', formula)
        self.assertIn('libexec/"bin/omnigent"', formula)
        self.assertNotRegex(formula, r"__(?:AGENTNEXUS|OMNIGENT|RESOURCES)[A-Z_]*__")

    def test_docker_source_and_bundle_copies_use_existing_package(self) -> None:
        for name in ("Dockerfile", "Dockerfile.ubi"):
            with self.subTest(name=name):
                source = (ROOT / "deploy/docker" / name).read_text(encoding="utf-8")
                package_copy = next(
                    line
                    for line in source.splitlines()
                    if re.match(r"COPY (?:agentnexus|omnigent)/", line)
                )
                _, origin, destination = package_copy.split()
                self.assertTrue((ROOT / origin).is_dir(), package_copy)
                self.assertEqual(origin, "agentnexus/")
                self.assertEqual(destination, "./agentnexus/")
                self.assertIn(
                    "COPY --from=web-builder /web/agentnexus/server/static/web-ui "
                    "./agentnexus/server/static/web-ui",
                    source,
                )

    def test_slack_upsert_preserves_existing_database_column(self) -> None:
        source = ROOT / "integrations/slack/src/agentnexus_slack/store.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        method = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "upsert_session"
        )
        query = next(
            node.value
            for node in ast.walk(method)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "INSERT INTO thread_sessions" in node.value
        )
        with sqlite3.connect(":memory:") as db:
            db.execute(
                "CREATE TABLE thread_sessions (team_id TEXT, channel_id TEXT, thread_ts TEXT, "
                "omnigent_session_id TEXT, title TEXT, owner_user_id TEXT, host_id TEXT, "
                "workspace TEXT, created_at INTEGER, updated_at INTEGER, "
                "PRIMARY KEY(team_id, channel_id, thread_ts))"
            )
            for session in ("old-session", "new-session"):
                db.execute(query, ("T1", "C1", "123", session, "title", "U1", "H1", "/work", 1, 2))
            self.assertEqual(
                db.execute("SELECT omnigent_session_id FROM thread_sessions").fetchall(),
                [("new-session",)],
            )


if __name__ == "__main__":
    unittest.main()
