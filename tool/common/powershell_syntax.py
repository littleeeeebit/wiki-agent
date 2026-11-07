"""Locate PowerShell code without treating string data as executable syntax."""

import re

ESCAPES = dict(zip("0abefnrtv", "\0\a\b\x1b\f\n\r\t\v"))
LABEL = r":[\w-]+"


class Refusal(ValueError):
    """An expected compatibility refusal, distinct from an internal analysis fault."""


def enclosed(value: str, opening: str, closing: str) -> bool:
    code, _ = syntax(value)
    if not code.startswith(opening):
        return False
    depth = 0
    for index, char in enumerate(code):
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if not depth:
                return index == len(code) - 1
    return False


def bound_span(prefix: str, name: str) -> tuple[int, int] | None:
    """Prove a literal binding while retaining the original source coordinates."""
    bare, _ = syntax(prefix)
    bindings = {}

    def literal(start, end):
        start += len(prefix[start:end]) - len(prefix[start:end].lstrip())
        source = prefix[start:end].rstrip()
        code, spans = syntax(source)
        if len(spans) == 1 and spans[0][0] == 0 and code.strip() == "0":
            source = source[:spans[0][1]]
            try:
                return (start, start + len(source)) if len(arguments(source)) == 1 else None
            except Refusal:
                return None
        source = source[:len(code.rstrip())].strip()
        if enclosed(source, "(", ")"):
            return literal(start + 1, start + len(source) - 1)
        container = re.sub(r"(?i)^\[ordered\]", "", source)
        if enclosed(source, "{", "}") or container.startswith("@") and (
            enclosed(container[1:], "{", "}") or enclosed(container[1:], "(", ")")
        ):
            return start, start + len(source)
        if match := re.fullmatch(r"\$(\w+)", source):
            return bindings.get(match[1].lower())
        return None

    def statement(start, end):
        code = bare[start:end]
        if not code.strip():
            return
        match = re.match(r"\s*(?P<cast>\[(?:System\.)?string\]\s*)?\$(?P<name>\w+)\s*=\s*", code, re.I)
        value = literal(start + match.end(), end) if match else None
        if value is None or match["cast"] and prefix[value[0]] in "{@[":
            bindings.clear()
        else:
            bindings[match["name"].lower()] = value

    depth, start = 0, 0
    for index, char in enumerate(bare):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif not depth and char in ";\n":
            statement(start, index)
            start = index + 1
    # Only an unevaluated assignment/return prefix may precede this call in the current statement.
    pending = r"\s*(?:(?:\[(?:System\.)?string\]\s*)?\$\w+\s*=\s*)*(?:(?:return|throw|exit)\s+)?[&.]\s*"
    if depth or not re.fullmatch(pending, bare[start:], re.I):
        return None
    return bindings.get(name.lower())


def bound_target(prefix: str, name: str) -> str | None:
    """Read a proved target without losing the shared binding validation."""
    span = bound_span(prefix, name)
    return prefix[slice(*span)] if span is not None else None


def command_position(prefix: str) -> bool:
    """Statement starts and complete assignment LHSs admit a command; arguments do not."""
    stack = []
    for index, char in enumerate(prefix):
        if char in "([{":
            stack.append([index, char == "{" and prefix[index - 1:index] == "@", None])
        elif char in ")]}" and stack:
            stack.pop()
        elif stack and stack[-1][1]:
            frame = stack[-1]
            if char == "=" and frame[2] is None:
                frame[2] = index + 1
            elif char == ";" or char == "\n" and frame[2] is not None and prefix[frame[2]:index].strip() \
                    and prefix[:index].rstrip()[-1:] not in ("|", "=", ",", "+", "-", "*", "/"):
                frame[2] = None
    if stack and stack[-1][1]:
        # An entry separator starts a key, not a statement. Values have their own statement context.
        start = stack[-1][2]
        return start is not None and command_position(prefix[start:])
    boundary = r"(?:^|[;&|{(\n])\s*"
    end = r"(?:(?:return|throw|exit)\s+)?(?:&\s*)?$"
    if re.search(boundary + end, prefix, re.I):
        return True
    variable = r"(?:\[[\w.,\s\[\]]+\]\s*)*\$(?:\{[^{}]+\}|[\w:]+)(?:\.[\w]+|\[[^\]\n;]*\])*"
    assignment = rf"{variable}(?:\s*,\s*{variable})*\s*(?:\?\?=|[-+*/%]?=)\s*"
    if re.search(boundary + rf"(?:{assignment})+" + end, prefix, re.I):
        return True
    iterator = re.search(rf"(?:{LABEL}\s+)?\bforeach\s*\(\s*{variable}\s+in\s*" + end, prefix, re.I)
    if iterator and command_position(prefix[:iterator.start()]):
        return True
    return False


def expanded(value: str) -> str:
    def replace(match):
        token = match[0]
        if token.startswith("$"):
            raise Refusal("실행 전에 PowerShell 명령 인수를 확정할 수 없다.")
        if token.startswith("`u{"):
            try:
                return chr(int(token[3:-1], 16))
            except ValueError as exc:
                raise Refusal("실행 전에 PowerShell 명령 인수를 확정할 수 없다.") from exc
        return ESCAPES.get(token[1], token[1])
    return re.sub(r"`(?:u\{[\da-fA-F]{1,6}\}|[\s\S])|\$[\w({]", replace, value)


def arguments(text: str) -> list[str]:
    """Read literal native arguments until the outer command ends; refuse computed values."""
    bare, spans = syntax(text)
    quoted = dict(spans)
    words, word, cursor, active = [], "", 0, False
    while cursor < len(text):
        if text[cursor] == "`":
            end = cursor + (3 if text.startswith("`\r\n", cursor) else 2)
            escaped = text[cursor + 1:end]
            if escaped not in ("\n", "\r\n"):
                word += ESCAPES.get(escaped, escaped)
                active = True
            cursor = end
            continue
        if (end := quoted.get(cursor)) is not None:
            value = text[cursor:end]
            quote = value[int(value.startswith("@"))]
            value = value.partition("\n")[2].rsplit("\n", 1)[0] if value.startswith("@") else value[1:-1]
            if quote == '"':
                if bare[cursor:end].strip() != "0":
                    raise Refusal("실행 전에 PowerShell 명령 인수를 확정할 수 없다.")
                value = expanded(value)
            else:
                value = value.replace("''", "'")
            word += value
            active, cursor = True, end
            continue
        char = bare[cursor]
        if char in ";|&\n)]}<>":
            break
        if char in "$({[" or char == "@" and re.match(r"@\w", text[cursor:]):
            raise Refusal("실행 전에 PowerShell 명령 인수를 확정할 수 없다.")
        if char.isspace():
            if active:
                words.append(word)
            word, active = "", False
        else:
            word += char
            active = True
        cursor += 1
    if active:
        words.append(word)
    return words


def bound_arguments(text: str, prefix: str) -> list[str]:
    """Read arguments after substituting only proved straight-line literal bindings."""
    bare, spans = syntax(text)
    variables = [(match.start(), match.end(), match[1]) for match in re.finditer(r"(?<!\S)\$(\w+)(?=[\s;|&)}]|$)", bare)]
    variables += [(start, end, match[1]) for start, end in spans
                  if (match := re.fullmatch(r'"\$(\w+)"', text[start:end]))]
    invocation = prefix if re.search(r"(?<!&)[&.]\s*$", syntax(prefix)[0]) else prefix + " & "
    for start, end, name in sorted(variables, reverse=True):
        value = bound_target(invocation, name)
        if value is not None:
            text = text[:start] + value + text[end:]
    return arguments(text)


def syntax(text: str) -> tuple[str, list[tuple[int, int]]]:
    """A same-length code view and quoted spans, retaining only string subexpressions."""
    view = list(text)
    strings = []
    size = len(text)

    def hide(start, end, value=False):
        view[start:end] = ["\n" if char == "\n" and not value else " " for char in text[start:end]]
        if value:
            view[start] = "0"

    def string(start, here):
        quote = text[start + int(here)]
        cursor = start + (3 if here else 1)
        expansions = []
        while cursor < size:
            if here and text.startswith("\n" + quote + "@", cursor):
                cursor += 3
                break
            if not here and text[cursor] == quote:
                if quote == "'" and text.startswith("''", cursor):
                    cursor += 2
                    continue
                cursor += 1
                break
            if quote == '"' and text[cursor] == "`":
                cursor += 2
                continue
            if quote == '"' and text.startswith("$(", cursor):
                end = walk(cursor + 2, ")")
                expansions.append((cursor, list(view[cursor:end])))
                cursor = end
            else:
                cursor += 1
        cursor = min(cursor, size)
        hide(start, cursor, value=True)
        for offset, code in expansions:
            view[offset:offset + len(code)] = code
        strings.append((start, cursor))
        return cursor

    def walk(cursor, closing=""):
        while cursor < size:
            char = text[cursor]
            if char == closing:
                return cursor + 1
            if text.startswith("<#", cursor):
                start, depth = cursor, 1
                cursor += 2
                while cursor < size and depth:
                    if text.startswith("<#", cursor):
                        depth += 1
                        cursor += 2
                    elif text.startswith("#>", cursor):
                        depth -= 1
                        cursor += 2
                    else:
                        cursor += 1
                hide(start, cursor)
            elif char == "#" and (cursor == 0 or text[cursor - 1].isspace() or text[cursor - 1] in ";|&({)]}"):
                end = text.find("\n", cursor)
                end = size if end < 0 else end
                hide(cursor, end)
                cursor = end
            elif char == "`":
                end = min(cursor + (3 if text.startswith("`\r\n", cursor) else 2), size)
                view[cursor:end] = [" "] * (end - cursor)
                cursor = end
            elif text[cursor:cursor + 2] in ("@'", '@"') and text[cursor + 2:cursor + 3] in ("\n", "\r"):
                cursor = string(cursor, True)
            elif char in "'\"":
                cursor = string(cursor, False)
            elif char in "([{":
                cursor = walk(cursor + 1, {"(": ")", "[": "]", "{": "}"}[char])
            else:
                cursor += 1
        return cursor

    walk(0)
    return "".join(view), sorted(strings)
