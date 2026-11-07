"""Resolve literal parameter collections without executing their contents."""

import re

from .powershell_syntax import Refusal, arguments, bound_span, enclosed, syntax


def parameters(source, position, name):
    """Return parameter names and original value spans from a proved splat."""
    bare, _ = syntax(source)
    prefix = source[:position]
    invocation = prefix if re.search(r"[&.]\s*$", bare[:position]) else prefix + " & "
    span = bound_span(invocation, name)
    if span is None:
        depth, start = 0, 0
        for index, char in enumerate(bare[:position]):
            if char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
            elif not depth and char in ";\n":
                start = index + 1
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

    def literal(start, end):
        start, end = trim(start, end)
        text, code = source[start:end], bare[start:end]
        if enclosed(text, "{", "}"):
            return True
        if re.fullmatch(r"0\s*|[+-]?\d+(?:\.\d+)?|\$(?:true|false|null)", code, re.I):
            return True
        if enclosed(text, "(", ")"):
            return literal(start + 1, end - 1)
        if text.startswith("@(") and enclosed(text[1:], "(", ")"):
            return all(literal(a, b) for a, b in parts(start + 2, end - 1, ","))
        return False

    start, end = span
    while enclosed(source[start:end], "(", ")"):
        start, end = trim(start + 1, end - 1)
    if source[start:end].lower().startswith("[ordered]"):
        start += len("[ordered]")
    text = source[start:end]
    if text.startswith("@{") and enclosed(text[1:], "{", "}"):
        result = []
        for first, last in parts(start + 2, end - 1, ";\n"):
            equal = bare.find("=", first, last)
            if equal < 0:
                raise Refusal("스플랫 매개 변수 이름을 실행 전에 확정할 수 없다.")
            names = arguments(source[first:equal])
            if len(names) != 1 or not re.fullmatch(r"[a-z][\w-]*", names[0], re.I) or not literal(equal + 1, last):
                raise Refusal("스플랫 매개 변수의 값을 실행 전에 확정할 수 없다.")
            result.append((names[0].lower(), *trim(equal + 1, last)))
        if len({key for key, _, _ in result}) != len(result):
            raise Refusal("중복된 스플랫 매개 변수를 실행 전에 확정할 수 없다.")
        return result
    if text.startswith("@(") and enclosed(text[1:], "(", ")"):
        result = [(None, *trim(first, last)) for first, last in parts(start + 2, end - 1, ",")]
        if all(literal(first, last) for _, first, last in result):
            return result
    if literal(start, end):
        return [(None, start, end)]
    raise Refusal("스플랫 매개 변수의 값을 실행 전에 확정할 수 없다.")
