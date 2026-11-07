"""Track declared executable aliases without evaluating their definitions."""

import re

from .powershell_syntax import Refusal, arguments, bound_target, syntax


class Aliases:
    def __init__(self, source: str):
        self.source = source
        self.bare = syntax(source)[0]
        self.values = {}
        self.uncertain = False

    def scope(self, position):
        stack = []
        for index, char in enumerate(self.bare[:position]):
            if char in "([{":
                stack.append((index, char == "{" and self.bare[index - 1:index] != "@"))
            elif char in ")]}" and stack:
                stack.pop()
        return tuple(index for index, block in stack if block)

    def resolve(self, name, position):
        current, seen, aliased = name.lower(), set(), False
        scope = self.scope(position) if self.values else ()
        while "/" not in current and "\\" not in current:
            if self.uncertain or current in seen:
                return None, True
            if current not in self.values:
                break
            value, declared = self.values[current]
            if value is None or scope[:len(declared)] != declared:
                return None, True
            seen.add(current)
            current, aliased = value.lower(), True
        return current.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".exe"), aliased

    def define(self, program, source, position):
        setters = ("set-alias", "new-alias", "sal", "nal")
        removers = ("remove-item", "ri", "rm", "rmdir", "rd", "del", "erase", "remove-alias")
        providers = ("set-item", "new-item", "si", "ni", *removers[:-1])
        if program not in (*setters, *providers, "remove-alias"):
            return
        bare, spans = syntax(source)
        variables = [(match.start(), match.end(), match[1]) for match in re.finditer(r"(?<!\S)\$(\w+)(?=[\s;|&)}]|$)", bare)]
        variables += [(start, end, match[1]) for start, end in spans
                      if (match := re.fullmatch(r'"\$(\w+)"', source[start:end]))]
        for start, end, name in sorted(variables, reverse=True):
            value = bound_target(self.source[:position] + " & ", name)
            if value is not None:
                source = source[:start] + value + source[end:]
        try:
            argv = arguments(source)
        except Refusal:
            self.uncertain = True
            return
        alias_path = any("alias:" in word.lower() for word in argv)
        if program in removers and (alias_path and any("," in word for word in argv)
                                    or not alias_path and re.search(r"\|\s*(?:[&.]\s*)?$", self.bare[:position])):
            self.uncertain = True
            return
        if program in providers:
            path_flags = [i for i, word in enumerate(argv[:-1]) if word.lower() in ("-path", "-literalpath")]
            if path_flags and not argv[path_flags[0] + 1].lower().startswith("alias:"):
                return
            if not any(word.lower().startswith("alias:") for word in argv):
                return
        fields, positional, index = {}, [], 0
        while index < len(argv):
            word = argv[index]
            if word.startswith("-"):
                key = word[1:].lower()
                if key in ("force", "passthru"):
                    index += 1
                    continue
                if key not in ("name", "value", "scope", "option", "options", "path", "literalpath", "description") or index + 1 == len(argv):
                    self.uncertain = True
                    return
                fields[key] = argv[index + 1]
                index += 2
            else:
                positional.append(word)
                index += 1
        if program in providers:
            path = fields.get("literalpath", fields.get("path"))
            if path is None:
                path = positional.pop(0) if positional else ""
            if not path.lower().startswith("alias:"):
                return
            name = path[6:].lstrip("\\/") or fields.get("name", "")
        else:
            name = fields["name"] if "name" in fields else positional.pop(0) if positional else ""
        value = fields["value"] if "value" in fields else positional.pop(0) if positional else None
        if not name or any(char in name for char in "*?[") or program in removers and ("," in name or value is not None or positional):
            self.uncertain = True
            return
        name = name.lower()
        scope = self.scope(position)
        previous = self.values.get(name)
        if program in removers:
            # A nested removal can reveal an outer alias whose overwritten binding is unknown.
            if not scope and fields.get("scope", "local").lower() in ("local", "0") and (previous is None or previous[0] is not None and previous[1] == scope):
                self.values.pop(name, None)
            else:
                self.values[name] = None, scope
            return
        if value is None or not value.strip() or fields.get("scope", "local").lower() not in ("local", "0") \
                or fields.get("option", fields.get("options", "none")).lower() != "none" \
                or previous and (previous[0] is None or program in ("new-alias", "nal", "new-item", "ni")):
            value = None
        self.values[name] = value, scope
