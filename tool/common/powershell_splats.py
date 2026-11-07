"""Resolve literal parameter collections without executing their contents."""

import re

from .powershell_syntax import Refusal, arguments, bound_span, enclosed, syntax


def parameters(source, position, name):
    """Return parameter names, source spans, literal shapes and captured block spans."""
    bare, _ = syntax(source)

    def statement_start(end):
        depth, start = 0, 0
        for index, char in enumerate(bare[:end]):
            if char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
            elif not depth and char in ";\n":
                start = index + 1
        return depth, start

    prefix = source[:position]
    invocation = prefix if re.search(r"[&.]\s*$", bare[:position]) else prefix + " & "
    span = bound_span(invocation, name)
    if span is None:
        depth, start = statement_start(position)
        # Only a literal pipeline input is proved not to mutate the collection before binding.
        pending = r"\s*(?:\$\w+\s*=\s*)*[\d\s,().@+-]+\|\s*(?:&\s*)?"
        if not depth and re.fullmatch(pending, bare[start:position]):
            span = bound_span(source[:start] + " & ", name)
    if span is None:
        raise Refusal("스플랫 매개 변수의 값을 실행 전에 확정할 수 없다.")

    def parts(start, end, separators):
        depth, first = 0, start
        for index in range(start, end):
            char = bare[index]
            if char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
            elif not depth and char in separators and not bare[first:index].rstrip().endswith("="):
                if bare[first:index].strip():
                    yield first, index
                first = index + 1
        if bare[first:end].strip():
            yield first, end

    def trim(start, end):
        start += len(bare[start:end]) - len(bare[start:end].lstrip())
        return start, start + len(bare[start:end].rstrip())

    def map_entries(start, end):
        start, end = trim(start, end)
        if source[start:end].lower().startswith("[ordered]"):
            start, end = trim(start + len("[ordered]"), end)
        text = source[start:end]
        if not text.startswith("@{") or not enclosed(text[1:], "{", "}"):
            return None
        result = []
        for first, last in parts(start + 2, end - 1, ";\n"):
            equal = bare.find("=", first, last)
            if equal < 0:
                raise Refusal("스플랫 매개 변수 이름을 실행 전에 확정할 수 없다.")
            names = arguments(source[first:equal])
            value = literal(equal + 1, last)
            if len(names) != 1 or value is None:
                raise Refusal("스플랫 매개 변수의 값을 실행 전에 확정할 수 없다.")
            result.append((names[0].lower(), *trim(equal + 1, last), *value))
        return result

    def array_values(start, end):
        text = source[start:end]
        if not text.startswith("@(") or not enclosed(text[1:], "(", ")"):
            return None
        result = []
        for first, last in parts(start + 2, end - 1, ",;\n"):
            value = literal(first, last)
            if value is None:
                return None
            result.append((*trim(first, last), *value))
        return result

    def literal(start, end):
        start, end = trim(start, end)
        text, code = source[start:end], bare[start:end]
        if enclosed(text, "{", "}"):
            return "0", [start]
        if re.fullmatch(r"0\s*|[+-]?\d+(?:\.\d+)?|\$(?:true|false|null)", code, re.I):
            return "0", []
        if variable := re.fullmatch(r"\$(\w+)", code):
            # A collection captures the value when its containing assignment executes.
            _, epoch = statement_start(start)
            captured = bound_span(source[:epoch] + " & ", variable[1])
            return literal(*captured) if captured is not None else None
        if enclosed(text, "(", ")"):
            return literal(start + 1, end - 1)
        if map_entries(start, end) is not None:
            # Entries are validated recursively, but their blocks and keys remain data.
            return "0", []
        values = array_values(start, end)
        if values is not None:
            return "@(" + ",".join(code for _, _, code, _ in values) + ")", [block for _, _, _, blocks in values for block in blocks]
        return None

    start, end = span
    while enclosed(source[start:end], "(", ")"):
        start, end = trim(start + 1, end - 1)
    result = map_entries(start, end)
    if result is not None:
        if any(not re.fullmatch(r"[a-z][\w-]*", entry[0], re.I) for entry in result):
            raise Refusal("스플랫 매개 변수 이름을 실행 전에 확정할 수 없다.")
        if len({entry[0] for entry in result}) != len(result):
            raise Refusal("중복된 스플랫 매개 변수를 실행 전에 확정할 수 없다.")
        return result
    values = array_values(start, end)
    if values is not None:
        return [(None, *value) for value in values]
    value = literal(start, end)
    if value is not None:
        return [(None, start, end, *value)]
    raise Refusal("스플랫 매개 변수의 값을 실행 전에 확정할 수 없다.")
