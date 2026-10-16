"""Minimal reference evaluator for the FFmpeg expression subset WayZoom emits.

Supported grammar: numbers, the variable ``t``, binary ``+ - * /``, unary minus,
parentheses, and the functions ``if(c,a,b)``, ``lt(a,b)``, ``gte(a,b)``.
Expressions are parsed once into a tree; ``if`` evaluates lazily like FFmpeg.
"""

from __future__ import annotations

import re
from collections.abc import Callable

_TOKEN = re.compile(r"\s*(\d+\.\d*|\d*\.\d+|\d+|[A-Za-z_]+|\S)")

Node = tuple


def _tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text)


class _Parser:
    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self, expected: str | None = None) -> str:
        token = self.tokens[self.pos]
        if expected is not None and token != expected:
            raise ValueError(f"expected {expected!r}, got {token!r}")
        self.pos += 1
        return token

    def expression(self) -> Node:
        node = self.term()
        while self.peek() in ("+", "-"):
            node = (self.take(), node, self.term())
        return node

    def term(self) -> Node:
        node = self.factor()
        while self.peek() in ("*", "/"):
            node = (self.take(), node, self.factor())
        return node

    def factor(self) -> Node:
        token = self.peek()
        if token == "-":
            self.take()
            return ("neg", self.factor())
        if token == "(":
            self.take()
            node = self.expression()
            self.take(")")
            return node
        token = self.take()
        if token == "t":
            return ("t",)
        if token in ("if", "lt", "gte"):
            self.take("(")
            args = [self.expression()]
            while self.peek() == ",":
                self.take()
                args.append(self.expression())
            self.take(")")
            return (token, *args)
        return ("num", float(token))


def _eval(node: Node, t: float) -> float:
    op = node[0]
    if op == "num":
        return node[1]
    if op == "t":
        return t
    if op == "neg":
        return -_eval(node[1], t)
    if op == "if":
        return _eval(node[2], t) if _eval(node[1], t) != 0 else _eval(node[3], t)
    a, b = _eval(node[1], t), _eval(node[2], t)
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if op == "/":
        return a / b
    if op == "lt":
        return 1.0 if a < b else 0.0
    if op == "gte":
        return 1.0 if a >= b else 0.0
    raise ValueError(f"unknown operator {op!r}")


def compile_expression(expression: str) -> Callable[[float], float]:
    """Parse ``expression`` once and return an evaluator of ``t``."""
    parser = _Parser(_tokenize(expression))
    tree = parser.expression()
    if parser.peek() is not None:
        raise ValueError(f"trailing tokens at {parser.pos}")
    return lambda t: _eval(tree, t)


def evaluate(expression: str, t: float) -> float:
    """Evaluate ``expression`` at time ``t``."""
    return compile_expression(expression)(t)
