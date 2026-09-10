"""Profile file lexer and context-sensitive argument parsing."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, replace
from enum import Enum, auto
from typing import Iterator

OPTION_RE = re.compile(r"^#\s*option\s+(.+)$")
ATTR_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*)\s*:(.*)$")

META_STARTER = "@meta"
HEADER_STARTER = "@header"
FROM_STARTER = "@from"


class TokenKind(Enum):
    BLANK = auto()
    COMMENT = auto()
    OPTION = auto()
    ATTR = auto()


class ArgRule(Enum):
    """How to parse raw text after NAME ':' on an attribute line."""

    SHELL_WORDS = auto()
    COMMA_LIST = auto()


@dataclass(frozen=True)
class ProfileLineToken:
    kind: TokenKind
    line: int
    name: str | None = None
    raw_arg: str | None = None
    arg: str | None = None
    args: tuple[str, ...] = ()
    option: str | None = None
    starter: str | None = None


ARG_RULES: dict[tuple[str, str], ArgRule] = {
    ("registered nameserver", "ns"): ArgRule.COMMA_LIST,
    ("hosts file", "host"): ArgRule.COMMA_LIST,
    ("hosts file", "hosts"): ArgRule.COMMA_LIST,
    ("zone file", "record"): ArgRule.COMMA_LIST,
    ("zone file", "name"): ArgRule.COMMA_LIST,
    ("bind db", "record"): ArgRule.COMMA_LIST,
    ("bind db", "name"): ArgRule.COMMA_LIST,
}

DEFAULT_ARG_RULE = ArgRule.SHELL_WORDS


def arg_rule(starter: str, name: str) -> ArgRule:
    return ARG_RULES.get((starter, name.lower()), DEFAULT_ARG_RULE)


def parse_shell_words(raw: str) -> list[str]:
    """Bash-like ARG*: whitespace-separated words with quotes and backslash escapes."""
    text = raw.strip()
    if not text:
        return []
    try:
        return shlex.split(text, posix=True)
    except ValueError:
        return text.split()


def parse_arg_list(raw: str, rule: ArgRule) -> list[str]:
    if rule == ArgRule.COMMA_LIST:
        parts: list[str] = []
        for segment in raw.split(","):
            segment = segment.strip()
            if not segment:
                continue
            parts.extend(parse_shell_words(segment))
        return parts
    return parse_shell_words(raw)


def parse_arg(raw: str, rule: ArgRule) -> str:
    args = parse_arg_list(raw, rule)
    if rule == ArgRule.COMMA_LIST:
        return ", ".join(args)
    return " ".join(args)


def canonical_ws_tokens(value: str) -> str:
    return " ".join(parse_shell_words(value))


def logical_lines(text: str) -> list[str]:
    """Join physical lines that end with \\ (shell-style continuation)."""
    logical: list[str] = []
    buf = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.endswith("\\"):
            buf += line[:-1]
            continue
        if buf:
            logical.append(buf + line)
            buf = ""
        else:
            logical.append(line)
    if buf:
        logical.append(buf)
    return logical


def lex_line(line: str, line_no: int) -> ProfileLineToken | None:
    stripped = line.strip()
    if not stripped:
        return ProfileLineToken(TokenKind.BLANK, line_no)
    if stripped.startswith("#"):
        opt_match = OPTION_RE.match(stripped)
        if opt_match:
            return ProfileLineToken(TokenKind.OPTION, line_no, option=opt_match.group(1).strip())
        return ProfileLineToken(TokenKind.COMMENT, line_no)
    match = ATTR_RE.match(stripped)
    if not match:
        return None
    return ProfileLineToken(
        TokenKind.ATTR,
        line_no,
        name=match.group(1).lower(),
        raw_arg=match.group(2),
    )


def _finalize_attr_token(
    token: ProfileLineToken,
    *,
    starter: str,
    rule: ArgRule,
) -> ProfileLineToken:
    raw_arg = token.raw_arg or ""
    args = tuple(parse_arg_list(raw_arg, rule))
    return replace(
        token,
        arg=parse_arg(raw_arg, rule),
        args=args,
        starter=starter,
    )


def tokenize_profile(text: str) -> list[ProfileLineToken]:
    """Lex profile text; emit NAME ':' ARG* tokens with context-specific ARG parsing."""
    tokens: list[ProfileLineToken] = []
    starter = HEADER_STARTER
    seen_from_or_type = False

    for line_no, line in enumerate(logical_lines(text), 1):
        token = lex_line(line, line_no)
        if token is None:
            continue
        if token.kind != TokenKind.ATTR:
            tokens.append(token)
            continue

        assert token.name is not None
        name = token.name

        if name == "type":
            rule = arg_rule(META_STARTER, "type")
            finalized = _finalize_attr_token(token, starter=META_STARTER, rule=rule)
            starter = finalized.arg or ""
            seen_from_or_type = True
            tokens.append(finalized)
            continue

        if name == "from":
            rule = arg_rule(META_STARTER, "from")
            finalized = _finalize_attr_token(token, starter=META_STARTER, rule=rule)
            starter = FROM_STARTER
            seen_from_or_type = True
            tokens.append(finalized)
            continue

        if not seen_from_or_type:
            context = HEADER_STARTER
        elif starter == FROM_STARTER:
            context = FROM_STARTER
        else:
            context = starter

        rule = arg_rule(context, name)
        tokens.append(_finalize_attr_token(token, starter=context, rule=rule))

    return tokens


def iter_attribute_lines(text: str) -> Iterator[ProfileLineToken]:
    """Yield parsed NAME ':' ARG* attribute tokens (skip blank, comment, option)."""
    for token in tokenize_profile(text):
        if token.kind == TokenKind.ATTR:
            yield token
