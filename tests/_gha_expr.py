# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Evaluate the GitHub Actions expression subset ci.yml uses, for workflow tests.

String-matching an ``if:`` pins its spelling, not what it decides. Evaluating it
against an event lets a test state the truth table instead: which events run a
job, and on which one a step may fail without failing the job.

Supported: string / boolean literals, context paths, ``==`` / ``!=``, ``!``,
``&&`` / ``||``, parentheses and the status functions. Anything else raises, so
an expression outside the subset fails the test rather than evaluating wrongly.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_TOKEN = re.compile(r"\s*(?:(&&|\|\||==|!=|!|\(|\))|'((?:[^']|'')*)'|([A-Za-z_][\w.\-]*))")
_WRAPPED = re.compile(r"^\s*\$\{\{(.*)\}\}\s*$", re.DOTALL)


class _Parser:
    def __init__(self, text: str, context: Mapping[str, str], status: Mapping[str, bool]) -> None:
        self._tokens = self._tokenise(text)
        self._pos = 0
        self._context = context
        self._status = status
        self.calls_status_function = False

    @staticmethod
    def _tokenise(text: str) -> list[tuple[str, str]]:
        tokens: list[tuple[str, str]] = []
        pos = 0
        while pos < len(text.rstrip()):
            match = _TOKEN.match(text, pos)
            if not match:
                raise ValueError(f"unsupported expression syntax at {text[pos:]!r}")
            op, string, ident = match.groups()
            if op is not None:
                tokens.append(("op", op))
            elif string is not None:
                tokens.append(("str", string.replace("''", "'")))
            else:
                tokens.append(("ident", ident))
            pos = match.end()
        return tokens

    def _peek(self) -> tuple[str, str] | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _take(self, value: str | None = None) -> tuple[str, str]:
        token = self._peek()
        if token is None or (value is not None and token[1] != value):
            raise ValueError(f"expected {value!r}, got {token!r}")
        self._pos += 1
        return token

    def parse(self) -> Any:
        result = self._or()
        if self._peek() is not None:
            raise ValueError(f"trailing tokens from {self._peek()!r}")
        return result

    def _or(self) -> Any:
        left = self._and()
        while self._peek() == ("op", "||"):
            self._take()
            right = self._and()
            left = left or right
        return left

    def _and(self) -> Any:
        left = self._not()
        while self._peek() == ("op", "&&"):
            self._take()
            right = self._not()
            left = left and right
        return left

    def _not(self) -> Any:
        if self._peek() == ("op", "!"):
            self._take()
            return not self._not()
        return self._compare()

    def _compare(self) -> Any:
        left = self._primary()
        token = self._peek()
        if token in {("op", "=="), ("op", "!=")}:
            self._take()
            right = self._primary()
            return (left == right) if token == ("op", "==") else (left != right)
        return left

    def _primary(self) -> Any:
        kind, value = self._take()
        if (kind, value) == ("op", "("):
            inner = self._or()
            self._take(")")
            return inner
        if kind == "str":
            return value
        if kind != "ident":
            raise ValueError(f"unexpected {value!r}")
        if value in {"true", "false"}:
            return value == "true"
        if self._peek() == ("op", "("):
            self._take()
            self._take(")")
            if value not in self._status:
                raise ValueError(f"unsupported function {value}()")
            self.calls_status_function = True
            return self._status[value]
        # An unset path (a skipped job's output) reads as an empty string, as on a runner.
        return self._context.get(value, "")


def evaluate(expr: object, context: Mapping[str, str], *, needs_succeeded: bool = True) -> bool:
    """Evaluate an ``if:`` / ``continue-on-error:`` value as a runner would.

    ``expr`` is the parsed YAML value: a bool, a bare expression, or one wrapped
    in ``${{ }}``. ``needs_succeeded`` is False when a ``needs:`` job was skipped
    or failed; an expression calling no status function then carries the
    runner's implicit ``success()`` and is False. The run is never cancelled.
    """
    if isinstance(expr, bool):
        return expr
    text = str(expr)
    wrapped = _WRAPPED.match(text)
    if wrapped:
        text = wrapped.group(1)
    status = {"cancelled": False, "always": True, "success": needs_succeeded, "failure": not needs_succeeded}
    parser = _Parser(text, context, status)
    result = bool(parser.parse())
    return result if parser.calls_status_function else result and needs_succeeded
