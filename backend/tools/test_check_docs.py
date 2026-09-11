"""注释检查器回归测试：以临时源码验证漏检、过期目录与解析边界，不加载业务应用。

目录：
- DocumentationTests：
  独立验证检查器，不调用 Django、网络、模型或实际数据库。
- DocumentationTests.test_definitions_inside_control_flow_keep_qualified_names：
  if、for 与异常处理器内的定义必须被发现，且限定名只增加函数或类层级。
- DocumentationTests.test_variables_exclude_imports_and_local_state：
  模块分支和解包赋值属于目录范围，导入、类成员及函数局部变量不属于该集合。
- DocumentationTests.test_catalog_reports_renamed_and_removed_symbols：
  重命名后必须同时报告缺失的新名称和残留的旧名称，不能只检查目录存在。
- DocumentationTests.test_catalog_rejects_empty_or_duplicate_descriptions：
  有名称无说明、重复条目和缺少段标题均不能形成有效目录。
- DocumentationTests.test_javascript_declarations_require_adjacent_jsdoc：
  具名箭头函数、导出函数和带回调默认值的方法均计数；缺少 JSDoc 的方法报缺失。
- DocumentationTests.test_javascript_ignores_comment_and_string_examples：
  注释或字符串中的伪函数声明不能计数；普通行注释不能充当函数 JSDoc。
- DocumentationTests.test_checker_detects_missing_nested_doc_and_skips_dependencies：
  端到端扫描必须发现嵌套函数缺注释，同时忽略依赖目录中的无效源码。
- DocumentationTests.test_checker_reports_javascript_catalog_and_syntax_errors：
  缺失 JS 函数注释、失效索引和 Python 语法错误均产生明确诊断。

关键变量：
（无模块级变量。）

设计说明：
执行说明：python tools/test_check_docs.py；测试只在临时目录写入虚构源码并自动清理，不加载业务应用。
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
    """独立验证检查器，不调用 Django、网络、模型或实际数据库。"""

    def test_definitions_inside_control_flow_keep_qualified_names(self):
        """if、for 与异常处理器内的定义必须被发现，且限定名只增加函数或类层级。"""
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
        """模块分支和解包赋值属于目录范围，导入、类成员及函数局部变量不属于该集合。"""
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
        """重命名后必须同时报告缺失的新名称和残留的旧名称，不能只检查目录存在。"""
        header = "目录：\n- old：旧函数。\n关键变量：\n（无）"
        problems = compare_catalog(header, "目录", {"new"})
        self.assertEqual(len(problems), 2)
        self.assertIn("missing: new", problems[0])
        self.assertIn("stale: old", problems[1])

    def test_catalog_rejects_empty_or_duplicate_descriptions(self):
        """有名称无说明、重复条目和缺少段标题均不能形成有效目录。"""
        for header in ("目录：\n- f：", "目录：\n- f：a\n- f：b", "目录中提到了 f"):
            with self.subTest(header=header), self.assertRaises(ValueError):
                catalog_entries(header, "目录")
        self.assertEqual(
            catalog_entries("目录：\n- f：\n  计算结果。\n关键变量：\n（无）", "目录"),
            {"f": " 计算结果。"},
        )

    def test_javascript_declarations_require_adjacent_jsdoc(self):
        """具名箭头函数、导出函数和带回调默认值的方法均计数；缺少 JSDoc 的方法报缺失。"""
        source = """/** 模块功能 */
const LIMIT = 1;
/** 辅助查询 */
const lookup = (id) => id;
/** 异步入口 */
export async function run() {}
/** 客户端 */
export class Client {
  /** 初始化回调 */
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
        """注释或字符串中的伪函数声明不能计数；普通行注释不能充当函数 JSDoc。"""
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
        """端到端扫描必须发现嵌套函数缺注释，同时忽略依赖目录中的无效源码。"""
        with tempfile.TemporaryDirectory(prefix="backend-doc-check-") as directory:
            root = Path(directory)
            (root / "sample.py").write_text(
                '''"""测试模块。
目录：
- outer：外层函数。
- outer.inner：嵌套函数。
关键变量：
（无）
"""
def outer():
    """只定义嵌套函数。"""
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
        """缺失 JS 函数注释、失效索引和 Python 语法错误均产生明确诊断。"""
        with tempfile.TemporaryDirectory(prefix="backend-doc-check-") as directory:
            root = Path(directory)
            (root / "bad.py").write_text("def broken(:", encoding="utf-8")
            (root / "sample.js").write_text(
                """/**
 * @module sample
 * 目录：
 * - old：已移除。
 * 关键变量：
 * （无）
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
