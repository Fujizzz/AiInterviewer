"""Responsibilities: Associate JavaScript declarations with adjacent JSDoc and file-level indexes.
Implementation: Parse JavaScript using the pinned Tree-sitter grammar and collect qualified
declarations, module bindings, and anonymous-function counts.
Related Modules: tools/check_docs.py invokes these helpers; tools/test_check_docs.py and
tools/test_docs_contract.py verify their contracts.
Declaration Index:
- parse_javascript: Load the required parser and reject recovered syntax-error trees.
- node_name: Extract stable names from identifiers, static properties, and member paths.
- binding_target: Associate function or object expressions with their binding and documentation
  anchor.
- binding_names: Extract bound names from variable patterns without counting property keys or
  default values.
- adjacent_jsdoc: Determine whether a declaration has an adjacent, meaningful JSDoc block.
- javascript_symbols: Return declaration names, module variables, and anonymous-function counts for
  a source file.
- JavascriptInspector: Store parser output and accumulated symbol-scan state for one source file.
- JavascriptInspector.__init__: Parse source and initialize declaration, variable, and
  anonymous-scope state.
- JavascriptInspector.visit: Traverse syntax nodes while maintaining declaration scopes and module
  bindings.
Variable Index:
- FUNCTION_TYPES: Tree-sitter node types representing functions, generators, arrows, and methods.
- CLASS_TYPES: Tree-sitter node types representing class declarations and expressions.
"""

import re
from collections import Counter

FUNCTION_TYPES = {
    "function_declaration",
    "function_expression",
    "generator_function_declaration",
    "generator_function",
    "arrow_function",
    "method_definition",
}
CLASS_TYPES = {"class_declaration", "class"}


def parse_javascript(source):
    """Functionality: Parse JavaScript source with the required pinned Tree-sitter grammar.
    Inputs: UTF-8 JavaScript source text.
    Outputs: The parsed syntax-tree root node.
    Logic: Load the parser dependency, parse bytes, then inspect recovered trees and report the
    first syntax-error position.
    Constraints: Missing dependencies and syntax errors raise explicit exceptions; no regex fallback
    or source dump is used.
    """
    try:
        import tree_sitter_javascript
        from tree_sitter import Language, Parser
    except ImportError as exc:
        raise RuntimeError(
            "JavaScript parser unavailable; run python -m pip install -r requirements-docs.txt"
        ) from exc
    root = (
        Parser(Language(tree_sitter_javascript.language())).parse(source.encode("utf-8")).root_node
    )
    if root.has_error:
        pending = [root]
        while pending:
            node = pending.pop()
            if node.is_error or node.is_missing:
                row, column = node.start_point
                raise SyntaxError(f"JavaScript parse error at {row + 1}:{column + 1}")
            pending.extend(reversed(node.children))
        raise SyntaxError("JavaScript parse error")
    return root


def node_name(node):
    """Functionality: Extract a stable textual name from a JavaScript syntax node.
    Inputs: A Tree-sitter node or None.
    Outputs: An identifier/member path, or None when no static name can be established.
    Logic: Handle identifiers, string properties, static computed properties, and member expressions
    recursively.
    Constraints: Dynamic computed names are not indexable.
    """
    if node is None:
        return None
    if node.type in {"identifier", "property_identifier", "private_property_identifier", "this"}:
        return node.text.decode("utf-8")
    if node.type == "string":
        name = node.text.decode("utf-8")[1:-1]
        return name if re.fullmatch(r"[^\W\d][\w$]*|\$[\w$]*", name) else None
    if node.type == "computed_property_name" and len(node.named_children) == 1:
        child = node.named_children[0]
        return node_name(child) if child.type == "string" else None
    if node.type == "member_expression":
        owner = node_name(node.child_by_field_name("object"))
        member = node_name(node.child_by_field_name("property"))
        return f"{owner}.{member}" if owner and member else None
    return None


def binding_target(node):
    """Functionality: Resolve a function or object expression to its external binding and
    documentation anchor.
    Inputs: A Tree-sitter expression node.
    Outputs: A pair containing the optional bound name and the node against which JSDoc adjacency is
    tested.
    Logic: Skip parentheses and inspect supported variable, pair, assignment, field, and
    single-callback registration parents.
    Constraints: Multi-declarator statements require separate documentation; a shared registration
    comment only documents a sole callback.
    """
    anchor = node
    while anchor.parent and anchor.parent.type == "parenthesized_expression":
        anchor = anchor.parent
    parent = anchor.parent
    if parent is None:
        return None, anchor
    field = {
        "variable_declarator": "name",
        "pair": "key",
        "assignment_expression": "left",
        "field_definition": "property",
    }.get(parent.type)
    value_field = "right" if parent.type == "assignment_expression" else "value"
    if field and parent.child_by_field_name(value_field) == anchor:
        name = node_name(parent.child_by_field_name(field))
        anchor = parent
        if parent.type == "variable_declarator":
            siblings = [n for n in parent.parent.named_children if n.type == "variable_declarator"]
            if len(siblings) == 1:
                anchor = parent.parent
        elif (
            parent.type == "assignment_expression" and parent.parent.type == "expression_statement"
        ):
            anchor = parent.parent
        return name, anchor
    if parent.type == "arguments" and parent.parent.parent.type == "expression_statement":
        if anchor.prev_named_sibling and anchor.prev_named_sibling.type == "comment":
            return None, anchor
        callbacks = [child for child in parent.named_children if child.type in FUNCTION_TYPES]
        if len(callbacks) == 1:
            return None, parent.parent.parent
    return None, anchor


def binding_names(node):
    """Functionality: Collect variable bindings from a JavaScript pattern.
    Inputs: A Tree-sitter binding-pattern node or None.
    Outputs: The set of declared identifier names.
    Logic: Recurse through object/array/rest patterns and use value sides of pairs and assignments.
    Constraints: Property keys and default-expression identifiers are excluded.
    """
    if node is None:
        return set()
    if node.type in {"identifier", "shorthand_property_identifier_pattern"}:
        return {node.text.decode("utf-8")}
    if node.type == "pair_pattern":
        return binding_names(node.child_by_field_name("value"))
    if node.type in {"assignment_pattern", "object_assignment_pattern"}:
        return binding_names(node.child_by_field_name("left"))
    if node.type in {"object_pattern", "array_pattern", "rest_pattern"}:
        return set().union(*(binding_names(child) for child in node.named_children))
    return set()


def adjacent_jsdoc(anchor, source):
    """Functionality: Determine whether a declaration has an adjacent meaningful JSDoc block.
    Inputs: A declaration anchor node and the original UTF-8 source bytes.
    Outputs: True only when a separate nonempty JSDoc block immediately precedes the declaration.
    Logic: Normalize export anchors, reject intervening text and module/file tags, then inspect
    comment content.
    Constraints: Empty comments and comments containing only tags do not document a declaration.
    """
    if anchor.parent and anchor.parent.type == "export_statement":
        anchor = anchor.parent
    previous = anchor.prev_named_sibling
    if previous is None or previous.type != "comment":
        return False
    text = previous.text.decode("utf-8")
    if not text.startswith("/**") or "@module" in text or "@file" in text:
        return False
    if source[previous.end_byte : anchor.start_byte].strip():
        return False
    lines = [re.sub(r"^\s*\* ?", "", line).strip() for line in text[3:-2].splitlines()]
    return any(line and not line.startswith("@") for line in lines)


class JavascriptInspector:
    """Functionality: Hold the state required to inspect one JavaScript syntax tree.
    Inputs: Source is supplied to the constructor.
    Outputs: Stores the parser root, encoded source, definitions, variable names, synthetic
    counters, and anonymous count.
    Logic: Explicit instance state keeps recursive traversal ownership visible and avoids closure
    reference cycles.
    Constraints: One inspector represents one source file.
    """

    def __init__(self, source):
        """Functionality: Initialize scanner state from JavaScript source.
        Inputs: UTF-8 source text.
        Outputs: A parsed root and empty scan-result collections.
        Logic: Parse the source and initialize symbol lists, variable set, and per-scope
        synthetic-name counters.
        Constraints: Parser dependency and syntax failures propagate.
        """
        self.root = parse_javascript(source)
        self.source = source.encode("utf-8")
        self.definitions = []
        self.variables = set()
        self.synthetic = Counter()
        self.anonymous = 0

    def visit(self, node, scope="", local=False):
        """Functionality: Traverse syntax nodes and record declarations, bindings, and anonymous
        functions.
        Inputs: A Tree-sitter node, qualified scope prefix, and flag indicating function-local
        scope.
        Outputs: Mutates the inspector result collections and recursively visits named children.
        Logic: Resolve each binding, derive static or synthetic names, test adjacent JSDoc, and
        track module bindings.
        Constraints: Dynamic method names and ambiguous declarations are rejected by the public
        extraction function.
        """
        child_scope = scope
        child_local = local
        if node.type == "variable_declarator" and not local:
            declaration = node.parent
            owner = declaration.parent
            if owner.type == "export_statement":
                owner = owner.parent
            if owner.type == "program" or declaration.type == "variable_declaration":
                self.variables.update(binding_names(node.child_by_field_name("name")))
        if node.type in FUNCTION_TYPES | CLASS_TYPES or node.type == "object":
            bound_name, anchor = binding_target(node)
            name = bound_name or node_name(node.child_by_field_name("name"))
            if node.type == "method_definition":
                if not name:
                    raise ValueError(f"unindexable method at line {node.start_point.row + 1}")
                modifier = next((n.type for n in node.children if n.type in {"get", "set"}), None)
                if modifier:
                    name += "." + modifier
            if not name:
                kind = "object" if node.type == "object" else "callback"
                self.synthetic[(scope, kind)] += 1
                name = f"{kind}{self.synthetic[(scope, kind)]}"
                if node.type in FUNCTION_TYPES:
                    self.anonymous += 1
            if node.type != "object":
                self.definitions.append(
                    (scope + name, node.start_point.row + 1, adjacent_jsdoc(anchor, self.source))
                )
            child_scope = scope + name + "."
            child_local = True
        for child in node.named_children:
            self.visit(child, child_scope, child_local)


def javascript_symbols(source):
    """Functionality: Extract JavaScript declarations and module variables from one source file.
    Inputs: JavaScript source text.
    Outputs: Declaration tuples (qualified name, one-based line, JSDoc-valid flag), variable-name
    set, and anonymous-function count.
    Logic: Traverse the parsed tree and reject duplicate qualified names.
    Constraints: Getter/setter names receive explicit suffixes; local bindings are excluded from
    module variables.
    """
    inspector = JavascriptInspector(source)
    inspector.visit(inspector.root)
    duplicates = [
        name
        for name, count in Counter(item[0] for item in inspector.definitions).items()
        if count > 1
    ]
    if duplicates:
        raise ValueError("ambiguous JavaScript symbols: " + ", ".join(sorted(duplicates)))
    return inspector.definitions, inspector.variables, inspector.anonymous
