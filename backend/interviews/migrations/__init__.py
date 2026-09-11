"""Django 迁移包。按依赖顺序应用 schema 与种子数据；历史迁移用于兼容已有本地库。

目录：
- 0001_initial：初始表；0002_seed_questions：题库种子。
- 0003_delete_streamprobe：移除历史诊断表；本包不定义运行时函数。
"""
