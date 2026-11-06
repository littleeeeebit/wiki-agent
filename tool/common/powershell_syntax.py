"""Locate PowerShell code without treating string data as executable syntax."""

import re

ESCAPES = dict(zip("0abefnrtv", "\0\a\b\x1b\f\n\r\t\v"))


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
    if re.search(boundary + rf"foreach\s*\(\s*{variable}\s+in\s*" + end, prefix, re.I):
        return True
    return False


def expanded(value: str) -> str:
    def replace(match):
        token = match[0]
        if token.startswith("$"):
            raise ValueError("실행 전에 PowerShell 명령 인수를 확정할 수 없다.")
        if token.startswith("`u{"):
            try:
                return chr(int(token[3:-1], 16))
            except ValueError as exc:
                raise ValueError("실행 전에 PowerShell 명령 인수를 확정할 수 없다.") from exc
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
                    raise ValueError("실행 전에 PowerShell 명령 인수를 확정할 수 없다.")
                value = expanded(value)
            else:
                value = value.replace("''", "'")
            word += value
            active, cursor = True, end
            continue
        char = bare[cursor]
        if char in ";|&\n)]}<>":
            break
        if char in "$({[":
            raise ValueError("실행 전에 PowerShell 명령 인수를 확정할 수 없다.")
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
