"""Responsibilities: Validate source declaration and module-variable indexes.
Implementation: Parse Python with AST and JavaScript with Tree-sitter; report missing, stale,
malformed, and undocumented entries without importing project code.
Related Modules: javascript_docs supplies JavaScript symbols; test_check_docs.py and
test_docs_contract.py verify parser contracts.
Declaration Index:
- iter_definitions: Yield Python definitions in lexical order with qualified names, including
  definitions nested in control flow.
- module_variables: Collect module-scope assignment names while excluding imports, function locals,
  and class attributes.
- catalog_entries: Parse one English index section into a name-to-description mapping and reject
  malformed or duplicate entries.
- compare_catalog: Compare documented and actual names in both directions without modifying source.
- source_files: Yield Python and JavaScript files deterministically while excluding generated and
  dependency directories.
- check_documentation: Check module indexes, declaration documentation, and module-variable indexes
  and return diagnostics with counts.
- main: Print diagnostics and coverage counts; return a nonzero status when any issue is found.
Variable Index:
- IGNORED_DIRS: Directory names excluded from the source traversal.
- DEFINITION_TYPES: Python AST node types that represent indexed functions, asynchronous functions,
  and classes.
"""

import ast
import os
import re
from pathlib import Path

from javascript_docs import javascript_symbols

IGNORED_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "test-results", "dist"}
DEFINITION_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def iter_definitions(tree, prefix=""):
    """Functionality: Yield Python declarations as (qualified_name, AST_node) pairs.
    Inputs: A parsed AST node and an optional qualified-name prefix.
    Outputs: A lexical-order iterator including declarations in control-flow branches.
    Logic: Extend the prefix only for classes and named functions; recurse through every child node.
    Constraints: Control-flow nodes do not create name components, and imported symbols are not
    declarations.
    """
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, DEFINITION_TYPES):
            name = prefix + node.name
            yield name, node
            yield from iter_definitions(node, name + ".")
        else:
            yield from iter_definitions(node, prefix)


def module_variables(tree):
    """Functionality: Return names assigned in module scope.
    Inputs: A parsed Python AST node.
    Outputs: A set of module-level assignment target names.
    Logic: Visit module and control-flow assignments recursively and extract Name store targets.
    Constraints: Imports, function locals, and class attributes are excluded; tuple targets are
    expanded.
    """
    names = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, DEFINITION_TYPES):
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.update(
                item.id
                for target in targets
                for item in ast.walk(target)
                if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store)
            )
        else:
            names.update(module_variables(node))
    return names


def catalog_entries(header, section):
    """Functionality: Parse a single declaration or variable index section.
    Inputs: The file-header text and exact section name.
    Outputs: A mapping from indexed symbol names to nonempty descriptions.
    Logic: Accept '- symbol: description' entries, indented description continuations, and the exact
    empty marker 'None'.
    Constraints: Section titles use an ASCII colon; malformed, duplicate, missing, or repeated
    sections raise ValueError.
    """
    lines = header.splitlines()
    marker = section + ":"
    if marker not in lines:
        raise ValueError(f"missing {section} section")
    if lines.count(marker) != 1:
        raise ValueError(f"duplicate {section} section")
    entries = {}
    name = None
    empty_marker_count = 0
    for line in lines[lines.index(marker) + 1 :]:
        if re.fullmatch(r"[A-Za-z][A-Za-z ]*:", line):
            break
        match = re.fullmatch(r"- ([\w$#.]+):\s*(.*)", line)
        if match:
            name, detail = match.groups()
            if name in entries:
                raise ValueError(f"duplicate catalog entry {name}")
            entries[name] = detail.strip()
        elif line.startswith("  ") and name:
            entries[name] += " " + line.strip()
        elif line.strip() and line.strip() != "None":
            raise ValueError(
                f"malformed {section} entry: expected '- symbol: description' or 'None'"
            )
        elif line == "None":
            empty_marker_count += 1
    empty = [name for name, detail in entries.items() if not detail.strip()]
    if empty:
        raise ValueError(f"catalog entries lack descriptions: {', '.join(empty)}")
    if not entries and empty_marker_count == 0:
        raise ValueError(f"empty {section} section must contain 'None'")
    if empty_marker_count > 1:
        raise ValueError(f"duplicate empty marker in {section} section")
    if entries and empty_marker_count:
        raise ValueError(f"{section} section cannot combine entries with 'None'")
    return entries


def compare_catalog(header, section, actual):
    """Functionality: Compare an index with actual declarations.
    Inputs: Header text, section name, and the set or sequence of actual names.
    Outputs: Human-readable missing, stale, or format diagnostics.
    Logic: Compare names in both directions after parsing the section.
    Constraints: Source text is never modified.
    """
    try:
        documented = set(catalog_entries(header, section))
    except ValueError as exc:
        return [str(exc)]
    problems = []
    if missing := set(actual) - documented:
        problems.append(f"{section} missing: {', '.join(sorted(missing))}")
    if stale := documented - set(actual):
        problems.append(f"{section} stale: {', '.join(sorted(stale))}")
    return problems


def source_files(root):
    """Functionality: Yield supported source files below a project root.
    Inputs: A filesystem root path.
    Outputs: A deterministic iterator of non-symlink Python, JS, and MJS paths.
    Logic: Sort directory and file names and prune ignored directories before descent.
    Constraints: Symbolic links and generated/dependency folders are not traversed.
    """
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(name for name in dirs if name not in IGNORED_DIRS)
        for name in sorted(files):
            path = Path(directory) / name
            if not path.is_symlink() and path.suffix in {".py", ".js", ".mjs"}:
                yield path


def check_documentation(root):
    """Functionality: Validate source documentation coverage below a project root.
    Inputs: A filesystem root containing Python and JavaScript source.
    Outputs: A pair of diagnostic strings and counts by language/declaration kind.
    Logic: Check indexes bidirectionally, require Python docstrings and adjacent JavaScript JSDoc,
    and count module variables.
    Constraints: Reads source only; parse and I/O failures are reported rather than skipped.
    """
    problems = []
    counts = {
        "python_modules": 0,
        "definitions": 0,
        "javascript_modules": 0,
        "javascript_definitions": 0,
        "javascript_anonymous_callbacks": 0,
    }
    for path in source_files(root):
        relative = path.relative_to(root)
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(relative)) if path.suffix == ".py" else None
        except (OSError, UnicodeError, SyntaxError) as exc:
            problems.append(f"{relative}: source inspection failed ({type(exc).__name__})")
            continue
        if path.suffix == ".py":
            counts["python_modules"] += 1
            header = ast.get_docstring(tree) or ""
            definitions = list(iter_definitions(tree))
            for name, node in definitions:
                counts["definitions"] += 1
                if not (ast.get_docstring(node) or "").strip():
                    problems.append(f"{relative}:{node.lineno}: undocumented {name}")
            symbols = [name for name, _ in definitions]
            variables = module_variables(tree)
        else:
            counts["javascript_modules"] += 1
            raw_header = source.split("*/", 1)[0]
            header = "\n".join(re.sub(r"^\s*\* ?", "", line) for line in raw_header.splitlines())
            if not source.startswith("/**") or "@module" not in header:
                problems.append(f"{relative}: missing JavaScript module header")
            try:
                definitions, variables, anonymous = javascript_symbols(source)
            except (SyntaxError, ValueError, RuntimeError) as exc:
                problems.append(
                    f"{relative}: source inspection failed ({type(exc).__name__}): {exc}"
                )
                continue
            counts["javascript_anonymous_callbacks"] += anonymous
            counts["javascript_definitions"] += len(definitions)
            symbols = [name for name, _, _ in definitions]
            for name, line, documented in definitions:
                if not documented:
                    problems.append(f"{relative}:{line}: undocumented {name}")
        for section, actual in (("Declaration Index", symbols), ("Variable Index", variables)):
            problems.extend(
                f"{relative}: {problem}" for problem in compare_catalog(header, section, actual)
            )
    return problems, counts


def main():
    """Functionality: Run the documentation checker for the backend source tree.
    Inputs: The location of this file determines the backend root.
    Outputs: Printed diagnostics and a process status integer, where 1 indicates issues.
    Logic: Invoke check_documentation, print every finding, and report coverage totals.
    Constraints: Performs no source writes or application imports.
    """
    problems, counts = check_documentation(Path(__file__).resolve().parents[1])
    for problem in problems:
        print(problem)
    print(f"Documentation coverage: {counts}; problems={len(problems)}")
    return int(bool(problems))


if __name__ == "__main__":
    raise SystemExit(main())
