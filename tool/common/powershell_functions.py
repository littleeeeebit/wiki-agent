"""Distinguish declared callables from external command candidates."""

from dataclasses import dataclass
import re

from .powershell_syntax import Refusal, arguments, command_position, syntax


@dataclass(frozen=True)
class Definition:
    name: str
    start: int
    end: int
    body: int | None
    kind: str = "function"


class Functions:
    # shortcut: only local syntax and literal provider paths are proved; use native binding evidence for dynamic session state.
    def __init__(self, source):
        self.source = source
        self.bare = syntax(source)[0]
        self.blocks, stack = {}, []
        for index, char in enumerate(self.bare):
            if char == "{":
                stack.append(index)
            elif char == "}" and stack:
                self.blocks[stack.pop()] = index + 1
        self.definitions, self.called = [], []
        pattern = r"\b(?:function|filter)\s+([\w:.-]+)\s*(?:\([^{}]*\)\s*)?(\{)"
        for match in re.finditer(pattern, self.bare, re.I):
            if command_position(self.bare[:match.start()]) and match.start(2) in self.blocks:
                self.definitions.append(Definition(match[1].lower(), match.start(), self.blocks[match.start(2)], match.start(2)))
        assignment = r"\$(?:function:([\w:.-]+)|\{function:([\w:.-]+)\})\s*=\s*"
        for match in re.finditer(assignment, self.bare, re.I):
            if not command_position(self.bare[:match.start()]):
                continue
            body = match.end() if match.end() in self.blocks else None
            end = self.blocks[body] if body is not None else match.end()
            self.definitions.append(Definition((match[1] or match[2]).lower(), match.start(), end, body,
                                               "function" if body is not None else "unresolved"))

    def containers(self, position):
        return sorted(start for start, end in self.blocks.items() if start < position < end
                      and self.bare[start - 1:start] not in ("@", "$"))

    def binding(self, name, position):
        if "/" in name or "\\" in name:
            return "external"
        result = "external"
        call_blocks = self.containers(position)
        bodies = {item.body: item for item in self.definitions if item.body is not None}
        for item in sorted(self.definitions, key=lambda row: row.start if row.body is not None and row.body < position < row.end else row.end):
            scope, _, short = item.name.rpartition(":")
            if short not in (name.lower(), "*"):
                continue
            inside = item.body is not None and item.body < position < item.end
            if item.end > position and not inside:
                # Calls inside a function may run after later declarations have executed.
                if any(block in bodies for block in call_blocks) and item.start > position:
                    result = "ambiguous"
                continue
            uncertain, visible = scope not in ("", "local", "script", "global", "private"), True
            for block in self.containers(item.start):
                if block in call_blocks:
                    continue
                owner = bodies.get(block)
                if owner:
                    if any(called == owner.name.rsplit(":", 1)[-1] and when < position for called, when in self.called):
                        uncertain = True
                    else:
                        visible = False
                    continue
                prefix = self.bare[:block].rstrip()
                if prefix.endswith("."):
                    continue
                if prefix.endswith("&"):
                    if scope not in ("script", "global"):
                        visible = False
                elif re.search(r"(?:\)|\belse|\btry|\bfinally|\bdo)\s*$", prefix, re.I):
                    uncertain = True
                else:
                    visible = False
            if not visible:
                continue
            if scope == "private" and any(block not in self.containers(item.start) for block in call_blocks):
                uncertain = True
            if item.kind == "unresolved":
                result = "unresolved"
            elif uncertain or item.kind == "ambiguous" or item.kind == "removed" and result in ("ambiguous", "unresolved"):
                result = "ambiguous"
            else:
                result = "external" if item.kind == "removed" else "function"
        return result

    def define(self, program, tail, position):
        """Update function state; return true only for proved Function-only targets."""
        writes = ("set-item", "new-item", "si", "ni")
        removes = ("remove-item", "ri", "rm", "rmdir", "rd", "del", "erase")
        clears = ("clear-item", "cli")
        if program not in (*writes, *removes, *clears) or not re.search(r"function:", tail, re.I):
            return False
        offset = len(self.source) - len(tail)
        body = next((start for start in sorted(self.blocks) if start >= offset
                     and not re.search(r"[;\n|&]", self.bare[offset:start])), None)
        header = tail[:body - offset] if body is not None else tail
        try:
            argv = arguments(header)
        except Refusal:
            argv = []
        fields, positional, uncertain, index = {}, [], False, 0
        while index < len(argv):
            word = argv[index]
            if word.startswith("-"):
                flag = word.lower()
                if flag in ("-force", "-passthru") or flag == "-value" and index + 1 == len(argv) and body is not None:
                    index += 1
                    continue
                if flag not in ("-path", "-literalpath", "-name", "-value") or index + 1 == len(argv):
                    uncertain = True
                    break
                fields[flag] = argv[index + 1]
                index += 2
            else:
                positional.append(word)
                index += 1
        path = fields.get("-literalpath", fields.get("-path", positional[0] if positional else ""))
        remaining = positional if "-path" in fields or "-literalpath" in fields else positional[1:]
        for word in remaining:
            if path.endswith(",") or word.startswith(","):
                path += word
            else:
                break
        function_only = path.lower().startswith("function:") and "," not in path and not uncertain
        if path and not path.lower().startswith("function:"):
            if any(part.lower().startswith("function:") for part in path.split(",")):
                self.definitions.append(Definition("*", position, position, None, "ambiguous"))
            return False
        if not path:
            # A later command's provider path must not change this command's binding state.
            if re.search(r"[;\n|&]", self.bare[offset:offset + len(header)]):
                return False
            name, kind = "*", "ambiguous"
        else:
            name = (path[9:].lstrip("\\/") or fields.get("-name", "")).lower()
            if not name or any(char in name for char in "*?[, "):
                name, uncertain = "*", True
            kind = "ambiguous" if uncertain else "removed" if program in (*removes, *clears) else "function"
            if program in writes and body is None:
                kind = "unresolved"
            if program in (*removes, *clears) and self.containers(position):
                kind = "ambiguous"
        end = self.blocks[body] if body is not None else position
        self.definitions.append(Definition(name, position, end, body if program in writes else None, kind))
        return function_only
