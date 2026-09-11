"""源码注释完整性检查，无数据库或外部依赖。

目录：iter_definitions（限定名遍历）；check_documentation（文件与函数检查）；main。
约束：Python 模块具有目录说明，类/函数具有 docstring；JS 模块具有文件头目录。
检查只验证覆盖率，具体技术准确性仍需代码评审与行为测试共同保证。
"""

import ast
from pathlib import Path


def iter_definitions(tree, prefix=""):
    """递归遍历具名类和函数，产生限定名与 AST 节点，忽略匿名回调。"""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = prefix + node.name
            yield name, node
            yield from iter_definitions(node, name + ".")


def check_documentation(root):
    """检查后端源码注释覆盖并返回问题列表及统计。

    方法：Python 通过 AST 获取真实 docstring；JS 检查显式模块头标记。
    副作用：只读源码，不修改文件；不把测试生成物或缓存视为源码。
    """
    problems = []
    counts = {"python_modules": 0, "definitions": 0, "javascript_modules": 0}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or {"__pycache__", "test-results"}.intersection(path.parts):
            continue
        if path.suffix not in {".py", ".js", ".mjs"}:
            continue
        relative = path.relative_to(root)
        source = path.read_text(encoding="utf-8")
        if path.suffix == ".py":
            tree = ast.parse(source, filename=str(relative))
            counts["python_modules"] += 1
            if "目录" not in (ast.get_docstring(tree) or ""):
                problems.append(f"{relative}: missing module directory")
            for name, node in iter_definitions(tree):
                counts["definitions"] += 1
                if not ast.get_docstring(node):
                    problems.append(f"{relative}:{node.lineno}: undocumented {name}")
        else:
            counts["javascript_modules"] += 1
            header = source.split("*/", 1)[0]
            if not source.startswith("/**") or "@module" not in header or "目录" not in header:
                problems.append(f"{relative}: missing JavaScript module directory")
    return problems, counts


def main():
    """输出检查结果；缺失注释时退出码为 1，适合本地和 CI 复用。"""
    problems, counts = check_documentation(Path(__file__).resolve().parents[1])
    for problem in problems:
        print(problem)
    print(f"Documentation coverage: {counts}; missing={len(problems)}")
    return int(bool(problems))


if __name__ == "__main__":
    raise SystemExit(main())
