"""
Safe Python code execution tool using a restricted environment.
Only allows safe builtins and math/string/json/datetime operations.
"""

from __future__ import annotations

import io
import json
import math
import sys
import textwrap
from contextlib import redirect_stdout
from typing import Any

from langchain_core.tools import tool

from core.logger import get_logger

log = get_logger(__name__)

# Allowed built-ins for code execution
_SAFE_BUILTINS: dict[str, Any] = {
    "print": print,
    "range": range,
    "len": len,
    "int": int,
    "float": float,
    "str": str,
    "bool": bool,
    "list": list,
    "dict": dict,
    "set": set,
    "tuple": tuple,
    "sorted": sorted,
    "reversed": reversed,
    "enumerate": enumerate,
    "zip": zip,
    "map": map,
    "filter": filter,
    "sum": sum,
    "min": min,
    "max": max,
    "abs": abs,
    "round": round,
    "isinstance": isinstance,
    "type": type,
    "repr": repr,
    "format": format,
    "math": math,
    "json": json,
    "__builtins__": {},
}


@tool
def execute_python(code: str) -> str:
    """
    Execute a Python code snippet in a safe, restricted environment and return
    the output. Ideal for data manipulation, algorithm demonstration,
    list processing, string operations, and mathematical computations.

    Allowed modules: math, json.
    Allowed builtins: print, range, len, int, float, str, bool, list, dict,
                      set, tuple, sorted, sum, min, max, abs, round, etc.
    NOT allowed: file I/O, network access, os, sys, subprocess, importlib.

    Args:
        code: Valid Python code to execute.

    Returns:
        stdout output of the code, or error traceback.
    """
    log.debug("Code execution requested")
    code = textwrap.dedent(code)

    stdout_buf = io.StringIO()
    local_ns: dict[str, Any] = {}

    try:
        compiled = compile(code, "<omniagent_sandbox>", "exec")
        with redirect_stdout(stdout_buf):
            exec(compiled, {"__builtins__": _SAFE_BUILTINS}, local_ns)  # noqa: S102
        output = stdout_buf.getvalue()
        return output if output.strip() else "(Code executed successfully with no output)"
    except SyntaxError as exc:
        return f"SyntaxError at line {exc.lineno}: {exc.msg}"
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
