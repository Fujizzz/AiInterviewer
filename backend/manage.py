"""Django 管理命令入口。设置模块路径后将命令参数交给框架，不包含业务逻辑。

目录：
- main：
  配置设置模块并委派 Django 命令行；参数由 sys.argv 原样传入，不隐式修改环境。

关键变量：
（无模块级变量。）
"""

import os
import sys


def main():
    """配置设置模块并委派 Django 命令行；参数由 sys.argv 原样传入，不隐式修改环境。"""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
