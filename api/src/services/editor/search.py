"""Structural safety checks for user-supplied source-search regex patterns."""

import importlib
from typing import Any

MAX_REGEX_PATTERN_LENGTH = 512
_REGEX_PARSER = importlib.import_module("re._parser")
_REPEAT_OPS = {"MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"}


def _op_name(op: object) -> str:
    """Return a stable name for a regex parser opcode."""
    return str(getattr(op, "name", op))


def _repeat_child(arg: Any) -> list[Any]:
    return list(arg[2])


def _repeat_is_optional(arg: Any) -> bool:
    return arg[0] == 0 and arg[1] == 1


def _subpattern_child(arg: Any) -> list[Any]:
    return list(arg[-1])


def _branch_children(arg: Any) -> list[list[Any]]:
    return [list(branch) for branch in arg[1]]


def _unwrap_subpatterns(tokens: list[Any]) -> list[Any]:
    while len(tokens) == 1 and _op_name(tokens[0][0]) == "SUBPATTERN":
        tokens = _subpattern_child(tokens[0][1])
    return tokens


def _regex_tokens_are_single_repeat(tokens: list[Any]) -> bool:
    tokens = _unwrap_subpatterns(tokens)
    return len(tokens) == 1 and _op_name(tokens[0][0]) in _REPEAT_OPS


def _token_prefix_signature(tokens: list[Any]) -> object:
    tokens = _unwrap_subpatterns(tokens)
    if not tokens:
        return ("EMPTY",)
    op, arg = tokens[0]
    op_name = _op_name(op)
    if op_name == "LITERAL":
        return ("LITERAL", arg)
    if op_name == "IN":
        return ("IN", tuple(arg))
    if op_name in _REPEAT_OPS:
        return ("REPEAT", _token_prefix_signature(_repeat_child(arg)))
    return (op_name,)


def _branch_has_overlapping_alternatives(branches: list[list[Any]]) -> bool:
    seen: set[object] = set()
    for branch in branches:
        signature = _token_prefix_signature(branch)
        if signature in seen:
            return True
        seen.add(signature)
    return False


def _regex_tokens_have_overlapping_branch(tokens: list[Any]) -> bool:
    for op, arg in tokens:
        op_name = _op_name(op)
        if op_name == "BRANCH" and _branch_has_overlapping_alternatives(
            _branch_children(arg)
        ):
            return True
        if op_name == "SUBPATTERN" and _regex_tokens_have_overlapping_branch(
            _subpattern_child(arg)
        ):
            return True
        if op_name in _REPEAT_OPS and _regex_tokens_have_overlapping_branch(
            _repeat_child(arg)
        ):
            return True
    return False


def _regex_tokens_have_risky_repeat(tokens: list[Any]) -> bool:
    for op, arg in tokens:
        op_name = _op_name(op)
        if op_name in _REPEAT_OPS:
            child = _repeat_child(arg)
            if (
                (_regex_tokens_are_single_repeat(child) and not _repeat_is_optional(arg))
                or _regex_tokens_have_overlapping_branch(child)
            ):
                return True
            if _regex_tokens_have_risky_repeat(child):
                return True
        elif op_name == "SUBPATTERN" and _regex_tokens_have_risky_repeat(
            _subpattern_child(arg)
        ):
            return True
        elif op_name == "BRANCH" and any(
            _regex_tokens_have_risky_repeat(branch)
            for branch in _branch_children(arg)
        ):
            return True
    return False


def _validate_regex_pattern(pattern: str) -> None:
    """Reject regex patterns that are too large or likely to cause backtracking."""
    if len(pattern) > MAX_REGEX_PATTERN_LENGTH:
        raise ValueError(
            f"Regex pattern exceeds {MAX_REGEX_PATTERN_LENGTH} characters"
        )
    tokens = list(_REGEX_PARSER.parse(pattern))
    if _regex_tokens_have_risky_repeat(tokens):
        raise ValueError("Regex pattern uses nested quantifiers")
