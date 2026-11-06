"""Locate PowerShell code without treating string data as executable syntax."""


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
            elif char == "#" and (cursor == 0 or text[cursor - 1].isspace() or text[cursor - 1] in ";|&({"):
                end = text.find("\n", cursor)
                end = size if end < 0 else end
                hide(cursor, end)
                cursor = end
            elif char == "`":
                hide(cursor, min(cursor + 2, size))
                cursor += 2
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
