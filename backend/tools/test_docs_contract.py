"""注释契约回归：验证双位置关联、真实语法节点、严格失败及检查器进程退出状态。

目录：
- ContractTests：
  用隔离临时源码验证检查器，不加载业务模块或真实密钥。
- ContractTests.check_source：
  在单文件临时目录执行完整检查，返回问题列表。
- ContractTests.test_both_documentation_locations_are_required：
  对 Python、JS 穷举两处注释的四种组合，仅同时满足时通过。
- ContractTests.test_blank_python_docstrings_are_rejected：
  空白 docstring 或普通行注释不能代替函数职责说明。
- ContractTests.test_python_async_decorated_and_nested_definitions：
  装饰器、异步方法和不同类中的同名函数按限定名关联。
- ContractTests.test_catalog_format_is_strict：
  错误分隔符、重复段和孤立正文均应失败，Unicode 名称应可索引。
- ContractTests.test_javascript_empty_or_module_comments_cannot_document_functions：
  模块头、空 JSDoc、纯标签及被普通注释隔开的文档都不能满足声明要求。
- ContractTests.test_javascript_full_syntax_and_qualified_names：
  验证生成器、多行方法、私有方法、访问器、对象方法和绑定表达式。
- ContractTests.test_anonymous_callbacks_are_indexed：
  匿名回调须具备目录与注释，同名内层函数分别归入各回调作用域。
- ContractTests.test_registration_comment_requires_single_callback：
  单回调注册可使用注册语句注释，多回调不能共用一个注释。
- ContractTests.test_javascript_template_expressions_are_code：
  模板插值中的实际函数需计数，字符串、正则和注释中的伪声明不能计数。
- ContractTests.test_module_bindings_follow_scope_and_destructuring：
  模块变量识别不依赖缩进，解构只提取绑定而非属性键或默认值。
- ContractTests.test_javascript_parse_errors_fail_closed：
  错误恢复树不能冒充解析成功，歧义和动态方法名也应明确失败。
- ContractTests.test_malformed_javascript_is_reported_by_file：
  整体检查保留文件路径及行列，语法错误必须造成问题输出。
- ContractTests.test_missing_parser_is_an_actionable_failure：
  开发依赖缺失必须明确报错，不跳过 JavaScript 或回退正则识别。
- ContractTests.test_cli_exit_status_and_repeated_tree_release：
  独立进程执行完整检查，正常依赖时成功，缺失依赖时返回非零状态。

关键变量：
（无模块级变量。）

设计说明：
本文件由 unittest discovery 收集；临时源码包含刻意不合规示例，不在实际项目生成残留文件。
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
    """用隔离临时源码验证检查器，不加载业务模块或真实密钥。"""

    def check_source(self, source, suffix=".py"):
        """在单文件临时目录执行完整检查，返回问题列表。"""
        with tempfile.TemporaryDirectory(prefix="backend-doc-contract-") as directory:
            path = Path(directory) / ("sample" + suffix)
            path.write_text(source, encoding="utf-8")
            return check_documentation(Path(directory))[0]

    def test_both_documentation_locations_are_required(self):
        """对 Python、JS 穷举两处注释的四种组合，仅同时满足时通过。"""
        for suffix in (".py", ".js"):
            for catalog in (False, True):
                for local in (False, True):
                    with self.subTest(suffix=suffix, catalog=catalog, local=local):
                        header = "目录：\n" + ("- work：完成工作。\n" if catalog else "（无）\n")
                        header += "关键变量：\n（无）\n"
                        if suffix == ".py":
                            body = '    """完成工作。"""\n' if local else ""
                            source = (
                                '"""模块。\n' + header + '"""\ndef work():\n' + body + "    pass\n"
                            )
                        else:
                            body = "/** 完成工作。 */\n" if local else ""
                            source = (
                                "/**\n@module sample\n"
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
        """空白 docstring 或普通行注释不能代替函数职责说明。"""
        for doc in ('"""   \n    """', "# 工作说明"):
            with self.subTest(doc=doc):
                problems = self.check_source("def work():\n    " + doc + "\n    pass\n")
                self.assertTrue(any("undocumented work" in p for p in problems))

    def test_python_async_decorated_and_nested_definitions(self):
        """装饰器、异步方法和不同类中的同名函数按限定名关联。"""
        source = (
            "class A:\n @decorator\n async def work(self):\n  def inner(): pass\n"
            "class B:\n def work(self): pass"
        )
        names = [name for name, _ in iter_definitions(ast.parse(source))]
        self.assertEqual(names, ["A", "A.work", "A.work.inner", "B", "B.work"])

    def test_catalog_format_is_strict(self):
        """错误分隔符、重复段和孤立正文均应失败，Unicode 名称应可索引。"""
        for header in ("目录：\n- work: wrong", "目录：\n目录：", "目录：\nwork"):
            with self.subTest(header=header), self.assertRaises(ValueError):
                catalog_entries(header, "目录")
        self.assertEqual(catalog_entries("目录：\n- 计算：说明", "目录"), {"计算": "说明"})

    def test_javascript_empty_or_module_comments_cannot_document_functions(self):
        """模块头、空 JSDoc、纯标签及被普通注释隔开的文档都不能满足声明要求。"""
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
        """验证生成器、多行方法、私有方法、访问器、对象方法和绑定表达式。"""
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
        """匿名回调须具备目录与注释，同名内层函数分别归入各回调作用域。"""
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
        """单回调注册可使用注册语句注释，多回调不能共用一个注释。"""
        one = javascript_symbols("/** register */ test('case', () => {});")[0]
        two = javascript_symbols("/** register */ use(() => {}, () => {});")[0]
        self.assertTrue(one[0][2])
        self.assertFalse(any(documented for _, _, documented in two))

    def test_javascript_template_expressions_are_code(self):
        """模板插值中的实际函数需计数，字符串、正则和注释中的伪声明不能计数。"""
        source = """const text = `function fake() {} ${(() => 1)()}`;
const pattern = /function phantom\\(\\)/;
/* function comment() {} */
/** real */ function work() {}"""
        definitions, _, anonymous = javascript_symbols(source)
        self.assertEqual([name for name, _, _ in definitions], ["callback1", "work"])
        self.assertEqual(anonymous, 1)

    def test_module_bindings_follow_scope_and_destructuring(self):
        """模块变量识别不依赖缩进，解构只提取绑定而非属性键或默认值。"""
        source = """  const {key: alias, value = defaultValue, ...rest} = input;
let [first, , ...tail] = input;
if (true) { var shared; let blockOnly; }
function f() { var local; }
class C { member = 1; }
"""
        _, variables, _ = javascript_symbols(source)
        self.assertEqual(variables, {"alias", "value", "rest", "first", "tail", "shared"})

    def test_javascript_parse_errors_fail_closed(self):
        """错误恢复树不能冒充解析成功，歧义和动态方法名也应明确失败。"""
        for source in ("function broken( {", "const x = () => {", "class C { work( }"):
            with self.subTest(source=source), self.assertRaises(SyntaxError):
                javascript_symbols(source)
        for source in ("function same() {} function same() {}", "const obj = { [method]() {} };"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                javascript_symbols(source)

    def test_malformed_javascript_is_reported_by_file(self):
        """整体检查保留文件路径及行列，语法错误必须造成问题输出。"""
        problems = self.check_source("function broken( {", ".js")
        self.assertTrue(
            any("sample.js" in p and "SyntaxError" in p and "1:" in p for p in problems)
        )

    def test_missing_parser_is_an_actionable_failure(self):
        """开发依赖缺失必须明确报错，不跳过 JavaScript 或回退正则识别。"""
        with patch.dict(sys.modules, {"tree_sitter_javascript": None}):
            problems = self.check_source("function work() {}", ".js")
        self.assertTrue(any("requirements-docs.txt" in p and "unavailable" in p for p in problems))

    def test_cli_exit_status_and_repeated_tree_release(self):
        """独立进程执行完整检查，正常依赖时成功，缺失依赖时返回非零状态。"""
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
