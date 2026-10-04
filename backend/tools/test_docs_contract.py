"""Responsibilities: Verify strict documentation-checker behavior for Python and JavaScript.
Implementation: Exercise index/local-comment association, parser errors, symbol qualification,
scope, and CLI status using isolated source fixtures.
Related Modules: check_docs and javascript_docs implement the contracts tested here.
Declaration Index:
- ContractTests: Group isolated parser and end-to-end checker contract cases.
- ContractTests.check_source: Run the documentation checker on one isolated temporary source file
  and return its diagnostics.
- ContractTests.test_both_documentation_locations_are_required: Verify that both the file index and
  declaration-local comment are
  required for Python and JavaScript.
- ContractTests.test_blank_python_docstrings_are_rejected: Verify that empty docstrings and ordinary
  comments do not document a Python
  function.
- ContractTests.test_python_async_decorated_and_nested_definitions: Verify qualified-name collection
  for decorated, asynchronous,
  nested, and repeated method
  names.
- ContractTests.test_catalog_format_is_strict: Reject malformed headings and delimiters while
  retaining valid Unicode symbol names.
- ContractTests.test_javascript_empty_or_module_comments_cannot_document_functions:
  Verify that empty,
  module-only,
  tag-only, and
  nonadjacent JSDoc
  cannot document a
  declaration.
- ContractTests.test_javascript_full_syntax_and_qualified_names: Verify JavaScript symbol extraction
  for generators, methods, accessors,
  private names, and bindings.
- ContractTests.test_anonymous_callbacks_are_indexed: Verify callback numbering, local
  documentation, and callback-scoped nested
  names.
- ContractTests.test_registration_comment_requires_single_callback: Allow a registration comment for
  one callback and reject sharing
  it across callbacks.
- ContractTests.test_javascript_template_expressions_are_code: Count actual template interpolation
  functions and ignore
  pseudo-declarations in strings,
  regexes, and comments.
- ContractTests.test_module_bindings_follow_scope_and_destructuring: Verify module binding
  extraction across indentation
  and destructuring forms.
- ContractTests.test_javascript_parse_errors_fail_closed: Reject JavaScript syntax errors, duplicate
  symbols, and dynamic method names.
- ContractTests.test_malformed_javascript_is_reported_by_file: Retain file and source-position
  diagnostics for malformed JavaScript.
- ContractTests.test_missing_parser_is_an_actionable_failure: Report a missing documentation parser
  as an actionable failure.
- ContractTests.test_cli_exit_status_and_repeated_tree_release: Verify checker process status with
  and without installed site packages
  and repeatedly release parser trees.
Variable Index:
None
"""

import ast
import gc
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from check_docs import catalog_entries, check_documentation, iter_definitions
from javascript_docs import javascript_symbols


class ContractTests(unittest.TestCase):
    """Functionality: Verify the documentation checker contract across Python and JavaScript.
    Inputs: Synthetic source fixtures and isolated process/module state.
    Outputs: unittest assertions for parsing, indexes, declaration notes, and diagnostics.
    Logic: Group focused tests around declaration discovery, index validation, syntax, and CLI
    execution.
    Constraints: Tests do not load Django application behavior or real secrets.
    """

    def check_source(self, source, suffix=".py"):
        """Functionality: Run a complete documentation check for one temporary source file.
        Inputs: Source text and a supported filename suffix.
        Outputs: The checker diagnostic list.
        Logic: Write the text into a temporary directory, invoke check_documentation, and
        automatically remove the directory.
        Constraints: Does not import application modules or modify project files.
        """
        with tempfile.TemporaryDirectory(prefix="backend-doc-contract-") as directory:
            path = Path(directory) / ("sample" + suffix)
            path.write_text(source, encoding="utf-8")
            return check_documentation(Path(directory))[0]

    def test_both_documentation_locations_are_required(self):
        """Functionality: Verify that both the file index and declaration-local comment are required
        for Python and JavaScript.
        Inputs: Synthetic source, checker state, and explicitly controlled dependencies relevant to
        this case.
        Outputs: unittest assertions over parsed names, documentation status, diagnostics, or
        process status.
        Logic: Build the smallest fixture that exercises the stated contract and compare observed
        behavior with the expected result.
        Constraints: Temporary source is isolated; external service behavior is not inferred from
        these unit-level checks.
        """
        for suffix in (".py", ".js"):
            for catalog in (False, True):
                for local in (False, True):
                    with self.subTest(suffix=suffix, catalog=catalog, local=local):
                        header = "Declaration Index:\n" + (
                            "- work: Completed.\n" if catalog else "None\n"
                        )
                        header += "Variable Index:\nNone\n"
                        if suffix == ".py":
                            body = '    """完成工作。"""\n' if local else ""
                            source = (
                                '"""Test module.\n'
                                + header
                                + '"""\ndef work():\n'
                                + body
                                + "    pass\n"
                            )
                        else:
                            body = "/** 完成工作。 */\n" if local else ""
                            source = (
                                "/**\n@module sample\nResponsibilities: Test module.\n"
                                + header
                                + "*/\n"
                                + body
                                + "function work() {}"
                            )
                        problems = self.check_source(source, suffix)
                        self.assertEqual(bool(problems), not (catalog and local))
                        self.assertEqual(any("undocumented work" in p for p in problems), not local)
                        self.assertEqual(any("missing: work" in p for p in problems), not catalog)

    def test_blank_python_docstrings_are_rejected(self):
        """Functionality: Verify that empty docstrings and ordinary comments do not document a
        Python function.
        Inputs: Synthetic source, checker state, and explicitly controlled dependencies relevant to
        this case.
        Outputs: unittest assertions over parsed names, documentation status, diagnostics, or
        process status.
        Logic: Build the smallest fixture that exercises the stated contract and compare observed
        behavior with the expected result.
        Constraints: Temporary source is isolated; external service behavior is not inferred from
        these unit-level checks.
        """
        for doc in ('"""   \n    """', "# Work note"):
            with self.subTest(doc=doc):
                problems = self.check_source("def work():\n    " + doc + "\n    pass\n")
                self.assertTrue(any("undocumented work" in p for p in problems))

    def test_python_async_decorated_and_nested_definitions(self):
        """Functionality: Verify qualified-name collection for decorated, asynchronous, nested, and
        repeated method names.
        Inputs: Synthetic source, checker state, and explicitly controlled dependencies relevant to
        this case.
        Outputs: unittest assertions over parsed names, documentation status, diagnostics, or
        process status.
        Logic: Build the smallest fixture that exercises the stated contract and compare observed
        behavior with the expected result.
        Constraints: Temporary source is isolated; external service behavior is not inferred from
        these unit-level checks.
        """
        source = (
            "class A:\n @decorator\n async def work(self):\n  def inner(): pass\n"
            "class B:\n def work(self): pass"
        )
        names = [name for name, _ in iter_definitions(ast.parse(source))]
        self.assertEqual(names, ["A", "A.work", "A.work.inner", "B", "B.work"])

    def test_catalog_format_is_strict(self):
        """Functionality: Reject malformed headings and delimiters while retaining valid Unicode
        symbol names.
        Inputs: Synthetic source, checker state, and explicitly controlled dependencies relevant to
        this case.
        Outputs: unittest assertions over parsed names, documentation status, diagnostics, or
        process status.
        Logic: Build the smallest fixture that exercises the stated contract and compare observed
        behavior with the expected result.
        Constraints: Temporary source is isolated; external service behavior is not inferred from
        these unit-level checks.
        """
        for header in (
            "Declaration Index:\n- work： wrong",
            "Declaration Index:\nDeclaration Index:",
            "Declaration Index:\nwork",
        ):
            with self.subTest(header=header), self.assertRaises(ValueError):
                catalog_entries(header, "Declaration Index")
        self.assertEqual(
            catalog_entries("Declaration Index:\n- 计算: Description", "Declaration Index"),
            {"计算": "Description"},
        )

    def test_javascript_empty_or_module_comments_cannot_document_functions(self):
        """Functionality: Verify that empty, module-only, tag-only, and nonadjacent JSDoc cannot
        document a declaration.
        Inputs: Synthetic source, checker state, and explicitly controlled dependencies relevant to
        this case.
        Outputs: unittest assertions over parsed names, documentation status, diagnostics, or
        process status.
        Logic: Build the smallest fixture that exercises the stated contract and compare observed
        behavior with the expected result.
        Constraints: Temporary source is isolated; external service behavior is not inferred from
        these unit-level checks.
        """
        for comment in (
            "/** */",
            "/** @module m */",
            "/**\n * @returns {number}\n */",
            "/** valid */\n// intervening",
            "// normal",
        ):
            with self.subTest(comment=comment):
                definitions, _, _ = javascript_symbols(comment + "\nfunction work() {}")
                self.assertFalse(definitions[0][2])

    def test_javascript_full_syntax_and_qualified_names(self):
        """Functionality: Verify JavaScript symbol extraction for generators, methods, accessors,
        private names, and bindings.
        Inputs: Synthetic source, checker state, and explicitly controlled dependencies relevant to
        this case.
        Outputs: unittest assertions over parsed names, documentation status, diagnostics, or
        process status.
        Logic: Build the smallest fixture that exercises the stated contract and compare observed
        behavior with the expected result.
        Constraints: Temporary source is isolated; external service behavior is not inferred from
        these unit-level checks.
        """
        source = """/** generator */ export default function* work() {}
/** first */ class A {
 /** multiline */ async work(
   value
 ) {}
 /** private */ #hide() {}
 /** read */ get value() {}
 /** write */ set value(v) {}
 /** field */ handler = (value) => value;
}
/** second */ class B { /** method */ work() {} }
const api = { /** object method */ work() {}, /** arrow */ run: (value) => value };
/** alias */ const helper = function internal() {};
/** assigned */ api.close = () => {};
"""
        definitions, _, _ = javascript_symbols(source)
        self.assertEqual(
            [name for name, _, _ in definitions],
            [
                "work",
                "A",
                "A.work",
                "A.#hide",
                "A.value.get",
                "A.value.set",
                "A.handler",
                "B",
                "B.work",
                "api.work",
                "api.run",
                "helper",
                "api.close",
            ],
        )
        self.assertTrue(all(documented for _, _, documented in definitions))

    def test_anonymous_callbacks_are_indexed(self):
        """Functionality: Verify callback numbering, local documentation, and callback-scoped nested
        names.
        Inputs: Synthetic source, checker state, and explicitly controlled dependencies relevant to
        this case.
        Outputs: unittest assertions over parsed names, documentation status, diagnostics, or
        process status.
        Logic: Build the smallest fixture that exercises the stated contract and compare observed
        behavior with the expected result.
        Constraints: Temporary source is isolated; external service behavior is not inferred from
        these unit-level checks.
        """
        source = """use(/** callback one */ () => { /** inside */ function same() {} });
use(/** callback two */ () => { function same() {} });"""
        definitions, _, anonymous = javascript_symbols(source)
        self.assertEqual(
            [name for name, _, _ in definitions],
            ["callback1", "callback1.same", "callback2", "callback2.same"],
        )
        self.assertEqual(anonymous, 2)
        self.assertTrue(definitions[0][2])
        self.assertFalse(definitions[-1][2])
        problems = self.check_source(source, ".js")
        self.assertTrue(any("undocumented callback2.same" in p for p in problems))

    def test_registration_comment_requires_single_callback(self):
        """Functionality: Allow a registration comment for one callback and reject sharing it across
        callbacks.
        Inputs: Synthetic source, checker state, and explicitly controlled dependencies relevant to
        this case.
        Outputs: unittest assertions over parsed names, documentation status, diagnostics, or
        process status.
        Logic: Build the smallest fixture that exercises the stated contract and compare observed
        behavior with the expected result.
        Constraints: Temporary source is isolated; external service behavior is not inferred from
        these unit-level checks.
        """
        one = javascript_symbols("/** register */ test('case', () => {});")[0]
        two = javascript_symbols("/** register */ use(() => {}, () => {});")[0]
        self.assertTrue(one[0][2])
        self.assertFalse(any(documented for _, _, documented in two))

    def test_javascript_template_expressions_are_code(self):
        """Functionality: Count actual template interpolation functions and ignore
        pseudo-declarations in strings, regexes, and comments.
        Inputs: Synthetic source, checker state, and explicitly controlled dependencies relevant to
        this case.
        Outputs: unittest assertions over parsed names, documentation status, diagnostics, or
        process status.
        Logic: Build the smallest fixture that exercises the stated contract and compare observed
        behavior with the expected result.
        Constraints: Temporary source is isolated; external service behavior is not inferred from
        these unit-level checks.
        """
        source = """const text = `function fake() {} ${(() => 1)()}`;
const pattern = /function phantom\\(\\)/;
/* function comment() {} */
/** real */ function work() {}"""
        definitions, _, anonymous = javascript_symbols(source)
        self.assertEqual([name for name, _, _ in definitions], ["callback1", "work"])
        self.assertEqual(anonymous, 1)

    def test_module_bindings_follow_scope_and_destructuring(self):
        """Functionality: Verify module binding extraction across indentation and destructuring
        forms.
        Inputs: Synthetic source, checker state, and explicitly controlled dependencies relevant to
        this case.
        Outputs: unittest assertions over parsed names, documentation status, diagnostics, or
        process status.
        Logic: Build the smallest fixture that exercises the stated contract and compare observed
        behavior with the expected result.
        Constraints: Temporary source is isolated; external service behavior is not inferred from
        these unit-level checks.
        """
        source = """  const {key: alias, value = defaultValue, ...rest} = input;
let [first, , ...tail] = input;
if (true) { var shared; let blockOnly; }
function f() { var local; }
class C { member = 1; }
"""
        _, variables, _ = javascript_symbols(source)
        self.assertEqual(variables, {"alias", "value", "rest", "first", "tail", "shared"})

    def test_javascript_parse_errors_fail_closed(self):
        """Functionality: Reject JavaScript syntax errors, duplicate symbols, and dynamic method
        names.
        Inputs: Synthetic source, checker state, and explicitly controlled dependencies relevant to
        this case.
        Outputs: unittest assertions over parsed names, documentation status, diagnostics, or
        process status.
        Logic: Build the smallest fixture that exercises the stated contract and compare observed
        behavior with the expected result.
        Constraints: Temporary source is isolated; external service behavior is not inferred from
        these unit-level checks.
        """
        for source in ("function broken( {", "const x = () => {", "class C { work( }"):
            with self.subTest(source=source), self.assertRaises(SyntaxError):
                javascript_symbols(source)
        for source in ("function same() {} function same() {}", "const obj = { [method]() {} };"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                javascript_symbols(source)

    def test_malformed_javascript_is_reported_by_file(self):
        """Functionality: Retain file and source-position diagnostics for malformed JavaScript.
        Inputs: Synthetic source, checker state, and explicitly controlled dependencies relevant to
        this case.
        Outputs: unittest assertions over parsed names, documentation status, diagnostics, or
        process status.
        Logic: Build the smallest fixture that exercises the stated contract and compare observed
        behavior with the expected result.
        Constraints: Temporary source is isolated; external service behavior is not inferred from
        these unit-level checks.
        """
        problems = self.check_source("function broken( {", ".js")
        self.assertTrue(
            any("sample.js" in p and "SyntaxError" in p and "1:" in p for p in problems)
        )

    def test_missing_parser_is_an_actionable_failure(self):
        """Functionality: Report a missing documentation parser as an actionable failure.
        Inputs: Synthetic source, checker state, and explicitly controlled dependencies relevant to
        this case.
        Outputs: unittest assertions over parsed names, documentation status, diagnostics, or
        process status.
        Logic: Build the smallest fixture that exercises the stated contract and compare observed
        behavior with the expected result.
        Constraints: Temporary source is isolated; external service behavior is not inferred from
        these unit-level checks.
        """
        with patch.dict(sys.modules, {"tree_sitter_javascript": None}):
            problems = self.check_source("function work() {}", ".js")
        self.assertTrue(any("requirements-docs.txt" in p and "unavailable" in p for p in problems))

    def test_cli_exit_status_and_repeated_tree_release(self):
        """Functionality: Verify checker process status with and without installed site packages and
        repeatedly release parser trees.
        Inputs: Synthetic source, checker state, and explicitly controlled dependencies relevant to
        this case.
        Outputs: unittest assertions over parsed names, documentation status, diagnostics, or
        process status.
        Logic: Build the smallest fixture that exercises the stated contract and compare observed
        behavior with the expected result.
        Constraints: Temporary source is isolated; external service behavior is not inferred from
        these unit-level checks.
        """
        for _ in range(30):
            javascript_symbols("/** work */ function work() { use(() => {}); }")
            gc.collect()
        checker = Path(__file__).with_name("check_docs.py")
        for flags, expected in (([], 0), (["-S"], 1)):
            with self.subTest(flags=flags):
                result = subprocess.run(
                    [sys.executable, *flags, "-X", "utf8", str(checker)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=30,
                )
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                if expected:
                    self.assertIn("requirements-docs.txt", result.stdout)


if __name__ == "__main__":
    unittest.main()
