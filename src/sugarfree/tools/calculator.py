from __future__ import annotations

import ast
import operator
from collections.abc import Mapping
from typing import Any, Callable

from sugarfree.tools.base import ToolExecutionError, ToolResult


class CalculatorTool:
    name = "calculator"
    description = "Evaluate a basic arithmetic expression."
    input_schema = {
        "type": "object",
        "properties": {"expression": {"type": "string"}},
        "required": ["expression"],
        "additionalProperties": False,
    }

    async def execute(self, arguments: Mapping[str, Any]) -> ToolResult:
        expression = str(arguments["expression"])
        try:
            value = _evaluate(ast.parse(expression, mode="eval").body)
        except (SyntaxError, ValueError, ZeroDivisionError, OverflowError) as exc:
            raise ToolExecutionError(f"invalid arithmetic expression: {exc}") from exc
        return ToolResult(content=str(value))


_BINARY: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _evaluate(node: ast.AST) -> int | float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left = _evaluate(node.left)
        right = _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("exponent is too large")
        return _BINARY[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_evaluate(node.operand))
    raise ValueError(f"unsupported expression: {type(node).__name__}")

