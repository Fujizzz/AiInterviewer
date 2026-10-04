"""Responsibilities: Verify the documentation checker's declaration and variable index contracts.
Implementation: Use temporary Python and JavaScript sources to exercise parser boundaries and
diagnostics.
Related Modules: check_docs and javascript_docs implement the inspected behavior.
Declaration Index:
- DocumentationTests: Exercise AST collection, index parsing, JSDoc adjacency, and end-to-end
  diagnostics.
- DocumentationTests.test_definitions_inside_control_flow_keep_qualified_names: Check qualified
  names in control
  flow.
- DocumentationTests.test_variables_exclude_imports_and_local_state: Check module-variable
  collection boundaries.
- DocumentationTests.test_catalog_reports_renamed_and_removed_symbols: Require missing and stale
  declaration reports.
- DocumentationTests.test_catalog_rejects_empty_or_duplicate_descriptions: Reject malformed, empty,
  duplicate, and absent
  sections.
- DocumentationTests.test_javascript_declarations_require_adjacent_jsdoc: Check declaration and
  callback JSDoc coverage.
- DocumentationTests.test_javascript_ignores_comment_and_string_examples: Ignore pseudo-declarations
  in text and ordinary
  comments.
- DocumentationTests.test_checker_detects_missing_nested_doc_and_skips_dependencies:
  Check nested docs
  and ignored
  dependency
  directories.
- DocumentationTests.test_checker_reports_javascript_catalog_and_syntax_errors: Check language
  diagnostics.
Variable Index:
None
"""

import ast
import tempfile
import unittest
from pathlib import Path

from check_docs import (
    catalog_entries,
    check_documentation,
    compare_catalog,
    iter_definitions,
    javascript_symbols,
    module_variables,
)


class DocumentationTests(unittest.TestCase):
    """Functionality: Verify documentation-index parser and checker contracts.
    Inputs: Synthetic Python and JavaScript snippets written only to temporary directories.
    Outputs: unittest assertions over names, counts, and emitted diagnostics.
    Logic: Exercise declaration discovery, bidirectional indexes, and syntax/JSDoc failure paths.
    Constraints: No Django, network, model, real database, or project source execution is required.
    """

    def test_definitions_inside_control_flow_keep_qualified_names(self):
        """Functionality: Verify declarations nested in control-flow nodes.
        Inputs: A parsed sample containing class, function, loop, exception, and branch
        declarations.
        Outputs: The expected lexical sequence of qualified names.
        Logic: Compare iter_definitions output against explicit class/function nesting.
        Constraints: Control-flow constructs do not add name components.
        """
        tree = ast.parse("""
class Suite:
    def run(self):
        for item in []:
            async def delayed():
                pass
        try:
            pass
        except Exception:
            def failed():
                pass
if True:
    def branch():
        pass
""")
        self.assertEqual(
            [name for name, _ in iter_definitions(tree)],
            ["Suite", "Suite.run", "Suite.run.delayed", "Suite.run.failed", "branch"],
        )

    def test_variables_exclude_imports_and_local_state(self):
        """Functionality: Verify module-level assignment collection boundaries.
        Inputs: Parsed assignments at module, branch, class, and function scope.
        Outputs: The set of module assignment target names.
        Logic: Compare module_variables output with module-scope names.
        Constraints: Imports, class members, and function locals are excluded.
        """
        tree = ast.parse("""
from elsewhere import imported
LIMIT = 4
left, right = (1, 2)
if True:
    OPTIONAL: str = "yes"
class State:
    member = 1
def work():
    local = 2
""")
        self.assertEqual(module_variables(tree), {"LIMIT", "left", "right", "OPTIONAL"})

    def test_catalog_reports_renamed_and_removed_symbols(self):
        """Functionality: Require missing and stale diagnostics after a symbol rename.
        Inputs: A header documenting an old declaration and an empty variable index, plus the actual
        new name.
        Outputs: Two mismatch diagnostics.
        Logic: Compare actual and documented names in both directions.
        Constraints: Index names and delimiters follow the English ASCII-colon format.
        """
        header = "Declaration Index:\n- old: Former function.\nVariable Index:\nNone"
        problems = compare_catalog(header, "Declaration Index", {"new"})
        self.assertEqual(len(problems), 2)
        self.assertIn("missing: new", problems[0])
        self.assertIn("stale: old", problems[1])

    def test_catalog_rejects_empty_or_duplicate_descriptions(self):
        """Functionality: Reject incomplete and ambiguous index syntax.
        Inputs: Headers with blank entries, duplicates, absent sections, or valid continuation text.
        Outputs: ValueError for malformed inputs and a mapping for valid entries.
        Logic: Check parser failures and continuation parsing.
        Constraints: Section headings and bullet delimiters use ASCII colons; an empty section uses
        None.
        """
        for header in (
            "Declaration Index:\n- f:",
            "Declaration Index:\n- f: a\n- f: b",
            "Declaration Index mentions f",
            "Declaration Index:\n- f： a\nVariable Index:\nNone",
            "目录：\n- f： a",
            "Variable Index:\nNone\nNone",
            "Variable Index:\n None",
            "Variable Index:\nNone ",
            "Variable Index:",
        ):
            with self.subTest(header=header), self.assertRaises(ValueError):
                catalog_entries(header, "Declaration Index")
        self.assertEqual(
            catalog_entries(
                "Declaration Index:\n- f: result\n  Additional detail.\nVariable Index:\nNone",
                "Declaration Index",
            ),
            {"f": "result Additional detail."},
        )
        self.assertEqual(catalog_entries("Variable Index:\nNone", "Variable Index"), {})

    def test_javascript_declarations_require_adjacent_jsdoc(self):
        """Functionality: Check JavaScript declaration and callback JSDoc coverage.
        Inputs: Source with documented declarations, one undocumented method, and a callback
        default.
        Outputs: Declaration documentation flags, module variables, and anonymous callback count.
        Logic: Parse symbols and compare exact names and flags.
        Constraints: A module header does not count as declaration documentation.
        """
        source = """/** @module sample
 * Responsibilities: sample module.
 * Declaration Index:
 * - lookup: helper.
 * - run: async entry.
 * - Client: client.
 * - Client.constructor: initialize.
 * - Client.constructor.callback1: callback.
 * - Client.close: close.
 * Variable Index:
 * - LIMIT: configured limit.
 */
const LIMIT = 1;
/** helper query */
const lookup = (id) => id;
/** asynchronous entry */
export async function run() {}
/** client */
export class Client {
  /** initialize callback */
  constructor(url, { onData = () => {} } = {}) {}
  close() {}
}
"""
        definitions, variables, anonymous = javascript_symbols(source)
        entries = {name: documented for name, _, documented in definitions}
        self.assertEqual(
            entries,
            {
                "lookup": True,
                "run": True,
                "Client": True,
                "Client.constructor": True,
                "Client.constructor.callback1": False,
                "Client.close": False,
            },
        )
        self.assertEqual(variables, {"LIMIT", "lookup"})
        self.assertEqual(anonymous, 1)

    def test_javascript_ignores_comment_and_string_examples(self):
        """Functionality: Ignore pseudo-declarations in comments and strings.
        Inputs: JavaScript with fake declarations in a block comment and template literal.
        Outputs: One real declaration marked undocumented.
        Logic: Inspect parser-derived definitions.
        Constraints: An ordinary line comment cannot satisfy JSDoc adjacency.
        """
        source = """/* function fake() {} */
const text = `
function phantom() {}
`;
// ordinary comment
function real() {}
"""
        definitions, _, _ = javascript_symbols(source)
        self.assertEqual(definitions, [("real", 6, False)])

    def test_checker_detects_missing_nested_doc_and_skips_dependencies(self):
        """Functionality: Check nested declaration documentation and traversal behavior.
        Inputs: Temporary documented source plus an invalid ignored dependency file.
        Outputs: One missing-doc diagnostic and counts for the inspected Python module.
        Logic: Run check_documentation over the temporary project tree.
        Constraints: Temporary files are cleaned up; dependency source is excluded.
        """
        with tempfile.TemporaryDirectory(prefix="backend-doc-check-") as directory:
            root = Path(directory)
            (root / "sample.py").write_text(
                '''"""Test module.
Declaration Index:
- outer: Outer function.
- outer.inner: Nested function.
Variable Index:
None
"""
def outer():
    """Defines a nested function."""
    if True:
        def inner():
            pass
''',
                encoding="utf-8",
            )
            dependency = root / ".venv"
            dependency.mkdir()
            (dependency / "broken.py").write_text("invalid python [", encoding="utf-8")
            problems, counts = check_documentation(root)
            self.assertEqual(counts["python_modules"], 1)
            self.assertEqual(counts["definitions"], 2)
            self.assertEqual(len(problems), 1)
            self.assertIn("undocumented outer.inner", problems[0])

    def test_checker_reports_javascript_catalog_and_syntax_errors(self):
        """Functionality: Check JavaScript index, JSDoc, and Python syntax diagnostics.
        Inputs: A malformed Python file and JavaScript with a stale index and undocumented
        declaration.
        Outputs: Diagnostics for syntax failure, missing documentation, and missing/stale names.
        Logic: Run the checker over a temporary source tree.
        Constraints: Parser dependency failures remain explicit checker errors.
        """
        with tempfile.TemporaryDirectory(prefix="backend-doc-check-") as directory:
            root = Path(directory)
            (root / "bad.py").write_text("def broken(:", encoding="utf-8")
            (root / "sample.js").write_text(
                """/**
 * @module sample
 * Declaration Index:
 * - old: Removed.
 * Variable Index:
 * None
 */
const LIMIT = 1;
function current() {}
""",
                encoding="utf-8",
            )
            problems, _ = check_documentation(root)
            output = "\n".join(problems)
            for expected in (
                "SyntaxError",
                "undocumented current",
                "missing: current",
                "stale: old",
                "missing: LIMIT",
            ):
                self.assertIn(expected, output)


if __name__ == "__main__":
    unittest.main()
