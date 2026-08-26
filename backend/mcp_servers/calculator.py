"""Demo MCP server: safe calculator (stdio transport)."""

from __future__ import annotations

import ast
import math
import operator
from typing import Any

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("calculator")

_BIN_OPS: dict[type[ast.operator], Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS: dict[type[ast.unaryop], Any] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_FUNCS: dict[str, Any] = {
    "abs": abs,
    "round": round,
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "floor": math.floor,
    "ceil": math.ceil,
}
_CONSTS: dict[str, float] = {"pi": math.pi, "e": math.e}


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval(node.operand))
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FUNCS
    ):
        return _FUNCS[node.func.id](*(_eval(a) for a in node.args))
    raise ValueError(f"unsupported expression element: {ast.dump(node)[:80]}")


@mcp.tool()
def eval_expr(expression: str) -> str:
    """Evaluate a math expression, e.g. "sqrt(2) * (3 + 4)".

    Supports + - * / // % **, abs/round/sqrt/sin/cos/tan/log/exp/floor/ceil,
    and constants pi / e. No variables, no assignments.
    """
    tree = ast.parse(expression, mode="eval")
    result = _eval(tree)
    return str(int(result)) if float(result).is_integer() else str(result)


@mcp.tool()
def convert_units(value: float, from_unit: str, to_unit: str) -> str:
    """Convert between simple units: km/mi, kg/lb, c/f (celsius/fahrenheit)."""
    key = (from_unit.lower(), to_unit.lower())
    if key == ("km", "mi"):
        return str(value * 0.621371)
    if key == ("mi", "km"):
        return str(value / 0.621371)
    if key == ("kg", "lb"):
        return str(value * 2.20462)
    if key == ("lb", "kg"):
        return str(value / 2.20462)
    if key == ("c", "f"):
        return str(value * 9 / 5 + 32)
    if key == ("f", "c"):
        return str((value - 32) * 5 / 9)
    raise ValueError(f"unsupported conversion: {from_unit} -> {to_unit}")


if __name__ == "__main__":
    mcp.run()
