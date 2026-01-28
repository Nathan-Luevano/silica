from __future__ import annotations

import ast
import sys
from pathlib import Path


def check_file(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    violations: list[str] = []
    nodes: list[ast.AST] = [tree]
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            nodes.append(node)
    seen = set()
    for node in nodes:
        if id(node) in seen:
            continue
        seen.add(id(node))
        if ast.get_docstring(node, clean=False) is not None:
            lineno = getattr(node, "lineno", 0)
            violations.append(f"{path}:{lineno}: docstring found")
    return violations


def main() -> int:
    root = Path("pysilica")
    all_violations: list[str] = []
    for path in root.rglob("*.py"):
        all_violations.extend(check_file(path))
    for path in Path("tests").rglob("*.py"):
        all_violations.extend(check_file(path))
    for path in Path("scripts").rglob("*.py"):
        if path.name == "no_docstrings.py":
            continue
        all_violations.extend(check_file(path))

    if all_violations:
        for v in all_violations:
            print(v, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
