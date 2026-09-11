"""inklite —— ink 子集的编译器 / 闸门。

只实现视觉小说真正用得到的那一小撮语法，换来两件事：
产物可被程序判定（死节点、成环、选项数），错误可原样回灌给模型重写。
"""

from .compiler import CompileError, compile_ink, compile_text
from .lint import LintIssue, lint_story

__all__ = ["CompileError", "compile_ink", "compile_text", "LintIssue", "lint_story"]
