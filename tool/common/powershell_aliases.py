"""Track default and declared executable aliases without evaluating definitions."""

import re

from .powershell_syntax import Refusal, bound_arguments, syntax

# Windows aliases used by the supported binding-changing operations.
DEFAULTS = {**dict.fromkeys(("ri", "rm", "rmdir", "rd", "del", "erase"), "remove-item"),
            "sal": "set-alias", "nal": "new-alias", "si": "set-item", "ni": "new-item", "cli": "clear-item", "ipmo": "import-module",
            "iex": "invoke-expression", "foreach": "foreach-object", "%": "foreach-object",
            "where": "where-object", "?": "where-object"}
READ_ONLY = DEFAULTS.keys() - {"rm", "rmdir", "rd", "del", "erase"}
ALL_SCOPE = {"del", "foreach", "%", "where", "?"}


class Aliases:
    def __init__(self, source: str):
        self.source = source
        self.bare = syntax(source)[0]
        self.values = {}
        self.removed = set()
        self.mutable_defaults = set()
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
                if current in DEFAULTS and current not in self.removed:
                    current, aliased = DEFAULTS[current], True
                    continue
                break
            value, declared = self.values[current]
            if value is None or scope[:len(declared)] != declared:
                return None, True
            seen.add(current)
            current, aliased = value.lower(), True
        return current, aliased

    def define(self, program, source, position):
        setters = ("set-alias", "new-alias", "sal", "nal")
        removers = ("remove-item", "ri", "rm", "rmdir", "rd", "del", "erase", "remove-alias")
        providers = ("set-item", "new-item", "si", "ni", *removers[:-1])
        if program not in (*setters, *providers, "remove-alias"):
            return
        try:
            argv = bound_arguments(source, self.source[:position])
        except Refusal:
            self.uncertain = True
            return
        alias_path = any("alias:" in word.lower() for word in argv)
        if program in removers and not alias_path and re.search(r"\|\s*(?:[&.]\s*)?$", self.bare[:position]):
            self.uncertain = True
            return
        if program in providers:
            path_flags = [i for i, word in enumerate(argv[:-1]) if word.lower() in ("-path", "-literalpath")]
            path = argv[path_flags[0] + 1] if path_flags else argv[0] if argv and not argv[0].startswith("-") else None
            if path is not None:
                if not any(part.lower().startswith("alias:") for part in path.split(",")) and not path.endswith(","):
                    return
            if not alias_path:
                return
        fields, positional, index, forced = {}, [], 0, False
        while index < len(argv):
            word = argv[index]
            if word.startswith("-"):
                key = word[1:].lower()
                if key in ("force", "passthru"):
                    forced = forced or key == "force"
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
            for word in positional:
                if path.endswith(",") or word.startswith(","):
                    path += word
                else:
                    break
            if "," in path and any(part.lower().startswith("alias:") for part in path.split(",")):
                self.uncertain = True
                return
            if not path.lower().startswith("alias:"):
                return
            name = path[6:].lstrip("\\/") or fields.get("name", "")
        else:
            name = fields["name"] if "name" in fields else positional.pop(0) if positional else ""
        value = fields["value"] if "value" in fields else positional.pop(0) if positional else None
        literal = "literalpath" in fields or program in setters and name.lower() in DEFAULTS
        initial = name.lower() in DEFAULTS and name.lower() not in self.removed and name.lower() not in self.mutable_defaults
        if initial and name.lower() in READ_ONLY & ALL_SCOPE and not forced \
                and (literal or not any(char in name for char in "*?[")):
            return
        # Replacing an AllScope alias without preserving that option fails even with Force.
        if initial and name.lower() in ALL_SCOPE and program in ("set-alias", "sal") and not {"option", "options"} & fields.keys():
            return
        if not name or not literal and any(char in name for char in "*?[") or program in removers and ("," in name or value is not None or positional):
            self.uncertain = True
            return
        name = name.lower()
        scope = self.scope(position)
        previous = self.values.get(name)
        if name in DEFAULTS and program in removers:
            # Removing a local binding can reveal an inherited alias in the caller's scope.
            self.values[name] = None, scope
            return
        reset = initial and program in ("set-item", "si") and forced and not scope and not {"option", "options"} & fields.keys()
        if initial and not reset:
            self.values[name] = None, scope
            return
        if program in removers:
            # A nested removal can reveal an outer alias whose overwritten binding is unknown.
            if not scope and fields.get("scope", "local").lower() in ("local", "0") and (previous is None or previous[0] is not None and previous[1] == scope):
                self.values.pop(name, None)
                self.removed.add(name)
            else:
                self.values[name] = None, scope
            return
        if value is None or not value.strip() or fields.get("scope", "local").lower() not in ("local", "0") \
                or name in READ_ONLY and name not in self.removed and ("option" in fields or "options" in fields) \
                or fields.get("option", fields.get("options", "none")).lower() != "none" \
                or previous and (previous[0] is None or program in ("new-alias", "nal", "new-item", "ni")) \
                or name in DEFAULTS and name not in self.removed and program in ("new-alias", "new-item"):
            value = None
        self.values[name] = value, scope
        if reset and value is not None:
            self.mutable_defaults.add(name)
