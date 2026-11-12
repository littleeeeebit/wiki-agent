"""Exact module-import allowances for refactoring test contracts."""

import ast


def without(body: list, allowed: set[str]) -> list:
    out = []
    for statement in body:
        if not isinstance(statement, (ast.Import, ast.ImportFrom)):
            out.append(statement)
            continue
        names = [alias for alias in statement.names if (alias.asname or alias.name).split(".")[0] not in allowed]
        if names:
            out.append(ast.Import(names=names) if isinstance(statement, ast.Import)
                       else ast.ImportFrom(module=statement.module, names=names, level=statement.level))
    return out


def changed(before: list, after: list) -> set[str]:
    def bindings(body: list) -> dict[str, list[tuple]]:
        found = {}
        for statement in body:
            if isinstance(statement, (ast.Import, ast.ImportFrom)):
                for alias in statement.names:
                    name = (alias.asname or alias.name).split(".")[0]
                    found.setdefault(name, []).append((type(statement).__name__, getattr(statement, "module", None),
                                                       getattr(statement, "level", 0), alias.name, alias.asname))
        return {name: sorted(values, key=repr) for name, values in found.items()}

    old, new = bindings(before), bindings(after)
    return {name for name in old.keys() | new.keys() if old.get(name) != new.get(name)}


def declared(values: list[str]) -> dict[str, set[str]]:
    found = {}
    for value in values:
        file, binding = value.rsplit("::", 1)
        found.setdefault(file, set()).add(binding)
    return found


def contract(spec: dict) -> tuple[set, dict, dict, dict]:
    tests = spec["refactor"]["detail"].get("tests") or {}
    entries = [*tests.get("remove", []), *tests.get("contract", [])]
    pinned = {item["test"]: item["blob"] for item in entries if item.get("blob")}
    names = [item["test"] for item in entries if not item.get("blob")]
    whole, named = {name for name in names if "::" not in name}, {}
    for name in names:
        if "::" in name:
            file, *parts = name.split("::")
            named.setdefault(file, set()).add(tuple(parts))
    return whole, named, pinned, declared(tests.get("imports", []))
