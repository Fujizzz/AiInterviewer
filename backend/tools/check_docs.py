"""后端源码注释检查：核对符号目录、模块变量索引及声明处注释，不导入业务模块。

目录：
- iter_definitions：
  按词法顺序递归产生 (限定名, AST 节点)，包含 if/for/try 内的定义。
- module_variables：
  提取模块作用域赋值的名称集合，排除导入符号、函数局部量和类属性。
- catalog_entries：
  解析“目录：”或“关键变量：”下的显式条目，返回名称到说明的映射。
- compare_catalog：
  比较声明集合与目录集合，分别报告缺少条目和已失效条目，不自动修正源码。
- source_files：
  确定性列出后端源码，遍历前剪除环境、依赖和测试输出目录，不跟随符号链接。
- check_documentation：
  检查后端源码注释覆盖并返回问题列表及统计。
- main：
  输出检查结果；缺失注释时退出码为 1，适合本地和 CI 复用。

关键变量：
- DEFINITION_TYPES：
  Python 中需要 docstring 和目录条目的具名函数、异步函数及类节点类型。
- IGNORED_DIRS：
  源码遍历前剪除的环境、依赖、缓存与测试输出目录名称。


设计说明：
Python 使用 AST；JavaScript 由 javascript_docs 的 Tree-sitter 解析器提取限定名。
检查器要求声明处注释和文件目录同时存在，并反向拒绝残留目录。
匿名 JavaScript 回调使用作用域内编号，同样要求两处注释；HTML/CSS、语义及提交原子性由人工核对。
"""

import ast
import os
import re
from pathlib import Path

from javascript_docs import javascript_symbols

IGNORED_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "test-results"}
DEFINITION_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def iter_definitions(tree, prefix=""):
    """按词法顺序递归产生 (限定名, AST 节点)，包含 if/for/try 内的定义。

    prefix 只在进入类或具名函数时扩展；控制流节点不增加名称层级。
    遍历所有子节点而非仅 body，避免漏掉处理器、else 分支和循环中的测试替身。
    """
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, DEFINITION_TYPES):
            name = prefix + node.name
            yield name, node
            yield from iter_definitions(node, name + ".")
        else:
            yield from iter_definitions(node, prefix)


def module_variables(tree):
    """提取模块作用域赋值的名称集合，排除导入符号、函数局部量和类属性。

    条件分支中的模块赋值仍属于模块状态；元组解包递归提取 Name 目标。
    类成员和关键局部状态由文件头的状态说明人工维护，不混入模块变量索引。
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
    """解析“目录：”或“关键变量：”下的显式条目，返回名称到说明的映射。

    格式为 '- 符号名：说明'，说明可在下一缩进行续写；空段使用“（无）”。
    下一个无缩进中文标题结束当前段。重复条目和空说明作为格式错误明确抛出。
    """
    lines = header.splitlines()
    marker = section + "："
    if marker not in lines:
        raise ValueError(f"missing {section} section")
    if lines.count(marker) != 1:
        raise ValueError(f"duplicate {section} section")
    entries = {}
    name = None
    for line in lines[lines.index(marker) + 1 :]:
        if re.fullmatch(r"[^\s\-].*：", line):
            break
        match = re.fullmatch(r"- ([\w$#.]+)：(.*)", line)
        if match:
            name, detail = match.groups()
            if name in entries:
                raise ValueError(f"duplicate catalog entry {name}")
            entries[name] = detail.strip()
        elif line.startswith("  ") and name:
            entries[name] += " " + line.strip()
        elif line.strip() and not re.fullmatch(r"（无[^）]*）[。]?", line):
            raise ValueError(f"malformed {section} entry: expected '- symbol：description'")
    empty = [name for name, detail in entries.items() if not detail.strip()]
    if empty:
        raise ValueError(f"catalog entries lack descriptions: {', '.join(empty)}")
    return entries


def compare_catalog(header, section, actual):
    """比较声明集合与目录集合，分别报告缺少条目和已失效条目，不自动修正源码。"""
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
    """确定性列出后端源码，遍历前剪除环境、依赖和测试输出目录，不跟随符号链接。"""
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(name for name in dirs if name not in IGNORED_DIRS)
        for name in sorted(files):
            path = Path(directory) / name
            if not path.is_symlink() and path.suffix in {".py", ".js", ".mjs"}:
                yield path


def check_documentation(root):
    """检查后端源码注释覆盖并返回问题列表及统计。

    方法：检查头部目录与实际符号双向一致、声明处注释以及模块变量索引。
    返回：(问题列表, 计数字典)。读取或解析失败计入问题，不跳过后报告成功。
    副作用：只读源码，不导入业务代码、不加载 .env、不修改文件。
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
        for section, actual in (("目录", symbols), ("关键变量", variables)):
            problems.extend(
                f"{relative}: {problem}" for problem in compare_catalog(header, section, actual)
            )
    return problems, counts


def main():
    """输出检查结果；缺失注释时退出码为 1，适合本地和 CI 复用。"""
    problems, counts = check_documentation(Path(__file__).resolve().parents[1])
    for problem in problems:
        print(problem)
    print(f"Documentation coverage: {counts}; problems={len(problems)}")
    return int(bool(problems))


if __name__ == "__main__":
    raise SystemExit(main())
