"""
Safe mathematical expression evaluator.
Uses Python's ast module instead of eval() for security.
Includes protection against CPU exhaustion attacks (e.g. 9**9**9, factorial(999999)).
"""

from __future__ import annotations

import ast
import math
import operator
from typing import Any

from langchain_core.tools import tool

from core.logger import get_logger

log = get_logger(__name__)

# Safety limits to prevent CPU exhaustion / DoS attacks
_MAX_EXPONENT       = 1_000       # 2**1000 is already astronomically large
_MAX_FACTORIAL_ARG  = 5_000       # factorial(5000) is already enormous
_MAX_EXPRESSION_LEN = 500         # Prevent parsing huge expressions

# Allowed operators
_OPERATORS: dict[type, Any] = {
    ast.Add:      operator.add,
    ast.Sub:      operator.sub,
    ast.Mult:     operator.mul,
    ast.Div:      operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod:      operator.mod,
    ast.Pow:      operator.pow,
    ast.USub:     operator.neg,
    ast.UAdd:     operator.pos,
}


def _safe_factorial(n: float | int) -> int:
    """Factorial with a hard upper limit to prevent CPU exhaustion."""
    n = int(n)
    if n < 0:
        raise ValueError("Factorial is not defined for negative numbers.")
    if n > _MAX_FACTORIAL_ARG:
        raise ValueError(
            f"Factorial argument too large (max {_MAX_FACTORIAL_ARG:,}). "
            "This would take too long to compute."
        )
    return math.factorial(n)


def _safe_pow(base: Any, exp: Any) -> Any:
    """Power operation with a hard upper limit on the exponent."""
    if isinstance(exp, (int, float)) and abs(exp) > _MAX_EXPONENT:
        raise ValueError(
            f"Exponent too large (max ±{_MAX_EXPONENT:,}). "
            "This would produce an astronomically large number."
        )
    return operator.pow(base, exp)


# Allowed math functions
_SAFE_NAMES: dict[str, Any] = {
    "abs":       abs,
    "round":     round,
    "min":       min,
    "max":       max,
    "sum":       sum,
    "sqrt":      math.sqrt,
    "log":       math.log,
    "log10":     math.log10,
    "log2":      math.log2,
    "sin":       math.sin,
    "cos":       math.cos,
    "tan":       math.tan,
    "asin":      math.asin,
    "acos":      math.acos,
    "atan":      math.atan,
    "floor":     math.floor,
    "ceil":      math.ceil,
    "pi":        math.pi,
    "e":         math.e,
    "inf":       math.inf,
    "factorial": _safe_factorial,   # Protected version
    "gcd":       math.gcd,
}

# Override the pow operator with the safe version
_OPERATORS[ast.Pow] = _safe_pow


def _safe_eval(node: ast.AST) -> float | int:
    """Recursively evaluate an AST node safely."""
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"Unsupported constant type: {type(node.value)}")
    if isinstance(node, ast.Name):
        if node.id in _SAFE_NAMES:
            return _SAFE_NAMES[node.id]  # type: ignore[return-value]
        raise NameError(f"Name '{node.id}' is not allowed")
    if isinstance(node, ast.BinOp):
        op_func = _OPERATORS.get(type(node.op))
        if op_func is None:
            raise TypeError(f"Operator {type(node.op).__name__} is not allowed")
        left = _safe_eval(node.left)
        right = _safe_eval(node.right)
        return op_func(left, right)
    if isinstance(node, ast.UnaryOp):
        op_func = _OPERATORS.get(type(node.op))
        if op_func is None:
            raise TypeError(f"Operator {type(node.op).__name__} is not allowed")
        return op_func(_safe_eval(node.operand))
    if isinstance(node, ast.Call):
        func = _safe_eval(node.func)
        if not callable(func):
            raise TypeError(f"'{func}' is not callable")
        args = [_safe_eval(a) for a in node.args]
        return func(*args)
    raise TypeError(f"Unsupported AST node type: {type(node).__name__}")


@tool
def calculate(expression: str) -> str:
    """
    Evaluate a mathematical expression safely.
    Supports basic arithmetic (+, -, *, /, **, %), common math functions
    (sqrt, log, sin, cos, tan, floor, ceil, factorial, etc.) and constants
    (pi, e).

    Args:
        expression: The mathematical expression to evaluate, e.g. "sqrt(2) * pi".

    Returns:
        The result as a string, or an error message.
    """
    log.debug("Calculator: %s", expression)

    if len(expression) > _MAX_EXPRESSION_LEN:
        return f"Error: Expression too long (max {_MAX_EXPRESSION_LEN} characters)."

    try:
        tree = ast.parse(expression.strip(), mode="eval")
        result = _safe_eval(tree)
        # Format nicely
        if isinstance(result, float) and result == int(result) and abs(result) < 1e15:
            return str(int(result))
        return str(result)
    except ZeroDivisionError:
        return "Error: Division by zero."
    except (ValueError, NameError, TypeError, SyntaxError) as exc:
        return f"Error: {exc}"
    except Exception as exc:
        log.error("Calculator unexpected error: %s", exc)
        return f"Calculation failed: {exc}"
