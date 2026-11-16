"""Example of a trusted JARVIS tool plugin."""

import ast
import operator

from jarvis.tools import Tool


OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def calculate(node):
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in OPS:
        left, right = calculate(node.left), calculate(node.right)
        if type(node.op) is ast.Pow and abs(right) > 12:
            raise ValueError("Exponent zu groß.")
        result = OPS[type(node.op)](left, right)
        if abs(result) > 1e15:
            raise ValueError("Ergebnis zu groß.")
        return result
    if isinstance(node, ast.UnaryOp) and type(node.op) in OPS:
        return OPS[type(node.op)](calculate(node.operand))
    raise ValueError("Nur Zahlen und + - * / ** sind erlaubt.")


def register(registry):
    async def run(args):
        expression = args["expression"]
        if len(expression) > 100:
            raise ValueError("Ausdruck zu lang.")
        return calculate(ast.parse(expression, mode="eval").body)

    registry.register(Tool("calculate", "Calculate an arithmetic expression.",
        {"type": "object", "properties": {"expression": {"type": "string"}},
         "required": ["expression"], "additionalProperties": False},
        "compute.local", False, run))
