"""基于 Tree-sitter 语法树建立 JavaScript 声明、声明处 JSDoc 与文件目录的关联。

目录：
- parse_javascript：
  加载固定开发依赖并解析源码；语法错误和缺失依赖均显式失败。
- node_name：
  提取静态标识符或成员路径；动态计算名称无法建立稳定索引时返回空。
- binding_target：
  将函数或对象表达式关联到变量、属性或赋值目标，并返回注释锚点。
- binding_names：
  从变量解构模式提取绑定名称，不把属性键和默认值误计为变量。
- adjacent_jsdoc：
  检查紧邻声明的独立、非空 JSDoc；模块头不能代替声明注释。
- javascript_symbols：
  递归扫描全部语法分支，返回全部函数/类声明、模块变量和匿名函数计数。
- JavascriptInspector：
  保存单文件扫描状态；显式对象避免递归闭包持有语法树的引用环。
- JavascriptInspector.__init__：
  解析源码并初始化独立的符号、变量与匿名作用域计数。
- JavascriptInspector.visit：
  维护函数、类和对象上下文，确保同名方法使用不同限定名。

关键变量：
- FUNCTION_TYPES：
  JavaScript 具名函数、生成器、箭头函数及方法的语法节点类型。
- CLASS_TYPES：
  类声明与类表达式的语法节点类型。

关键状态说明：
JavascriptInspector.root 拥有语法树根；source 保存 UTF-8 字节以匹配解析器位置。
definitions 与 variables 分别累计声明和模块绑定；synthetic 按作用域编号，anonymous 为匿名函数数。

设计说明：
仅静态读取源码，不执行 JavaScript。匿名函数使用 callbackN 作为目录名称，
无绑定对象使用 objectN 作用域，编号按同层源码顺序；移动回调时必须复核关联说明。
动态计算方法名拒绝检查成功，必须人工选用可索引的静态名称。
本模块验证结构关联，不证明 JSDoc 语义、运行时语法约束或 Git 提交原子性。
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
    """加载固定开发依赖并解析源码；语法错误和缺失依赖均显式失败。

    Tree-sitter 会恢复错误并继续生成树，因此必须检查 has_error 后再提取符号。
    不使用正则回退；错误包含行列和安装命令，不输出可能包含敏感数据的源码。
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
    """提取静态标识符或成员路径；动态计算名称无法建立稳定索引时返回空。"""
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
    """将函数或对象表达式关联到变量、属性或赋值目标，并返回注释锚点。

    跳过括号包装；变量前与属性前的 JSDoc 属于对应初始化表达式。
    多变量声明须在各 declarator 前单独注释，避免一个注释被多个函数共用。
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
    """从变量解构模式提取绑定名称，不把属性键和默认值误计为变量。"""
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
    """检查紧邻声明的独立、非空 JSDoc；模块头不能代替声明注释。

    注释与声明间只允许空白。仅含标签或星号的 JSDoc 不构成职责说明。
    export 外层是声明锚点，支持 export default、异步和生成器声明。
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
    """保存单文件扫描状态；显式对象避免递归闭包持有语法树的引用环。"""

    def __init__(self, source):
        """解析源码并初始化独立的符号、变量与匿名作用域计数。"""
        self.root = parse_javascript(source)
        self.source = source.encode("utf-8")
        self.definitions = []
        self.variables = set()
        self.synthetic = Counter()
        self.anonymous = 0

    def visit(self, node, scope="", local=False):
        """维护函数、类和对象上下文，确保同名方法使用不同限定名。"""
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
    """递归扫描全部语法分支，返回全部函数/类声明、模块变量和匿名函数计数。

    每项声明为 (限定名, 一基行号, 独立 JSDoc 是否有效)。别名绑定使用外部可访问名；
    getter/setter 使用 .get/.set 后缀。相同限定名重复定义时拒绝产生歧义关联。
    模块变量包括 program 直属声明、顶层块中的 var，排除函数和类内绑定。
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
