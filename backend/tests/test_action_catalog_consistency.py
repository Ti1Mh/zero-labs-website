"""Automated consistency test between require("...") calls and ACTION_CATALOG."""

import ast
import re
from pathlib import Path

from app.auth.permissions import ACTION_CATALOG


def get_require_calls_from_ast(file_path: Path) -> list[tuple[int, str]]:
    """Extract action strings from require(...) calls using AST."""
    calls = []
    try:
        content = file_path.read_text(encoding="utf-8-sig")
        tree = ast.parse(content, filename=str(file_path))
    except Exception as exc:
        raise RuntimeError(f"Failed to parse {file_path}: {exc}") from exc

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # Check for direct require("action")
            func_name = None
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr

            if func_name == "require" and node.args:
                first_arg = node.args[0]
                if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                    calls.append((node.lineno, first_arg.value))

    return calls


def test_all_require_calls_exist_in_action_catalog():
    """Verify that every require('...') call in app/ corresponds to a valid ACTION_CATALOG key."""
    app_dir = Path(__file__).parent.parent / "app"
    assert app_dir.exists() and app_dir.is_dir(), f"app directory not found at {app_dir}"

    catalog_keys = set(ACTION_CATALOG.keys())
    all_found_calls = []
    invalid_calls = []

    for py_file in app_dir.rglob("*.py"):
        # Skip definition of require itself in dependencies.py
        calls = get_require_calls_from_ast(py_file)
        for lineno, action in calls:
            all_found_calls.append((py_file.name, lineno, action))
            if action not in catalog_keys:
                invalid_calls.append((str(py_file), lineno, action))

    # Guard: Ensure we actually found require calls in the codebase
    assert len(all_found_calls) >= 5, (
        f"Expected to find at least 5 require(...) calls in {app_dir}, found only {len(all_found_calls)}"
    )

    # Main assertion: No undefined actions
    assert not invalid_calls, (
        f"Found {len(invalid_calls)} require() calls with actions NOT in ACTION_CATALOG: {invalid_calls}"
    )


def test_regex_consistency_check():
    """Secondary check using regex to catch any calls not identified by AST."""
    app_dir = Path(__file__).parent.parent / "app"
    catalog_keys = set(ACTION_CATALOG.keys())
    pattern = re.compile(r'require\(\s*["\']([^"\']+)["\']\s*\)')

    found_actions = set()
    for py_file in app_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8-sig")
        matches = pattern.findall(content)
        for match in matches:
            found_actions.add(match)
            assert match in catalog_keys, f"Action '{match}' in {py_file} not in ACTION_CATALOG!"

    assert len(found_actions) >= 5
