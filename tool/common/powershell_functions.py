"""Distinguish declared callables from external command candidates."""

from dataclasses import dataclass
import re

from .powershell_syntax import LABEL, Refusal, bound_arguments, command_position, syntax
from .powershell_splats import parameters


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
        self.bare, strings = syntax(source)
        self.strings = dict(strings)
        self.blocks, stack = {}, []
        for index, char in enumerate(self.bare):
            if char == "{":
                stack.append(index)
            elif char == "}" and stack:
                self.blocks[stack.pop()] = index + 1
        self.definitions, self.called = [], []
        self.callbacks = {}
        self.splatted = set()
        self.parallel = set()
        self.statements = set()
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
        if re.search(r"(?im)^\s*using\s+module\b", self.bare):
            self.note_import(-1, -1)
        # Requirements are comments to the syntax view, but can load modules before execution.
        quoted, cursor, depth = dict(strings), 0, 0
        requirements = "#requires" in source.lower()
        while requirements and cursor < len(source):
            if not depth and cursor in quoted:
                cursor = quoted[cursor]
            elif source.startswith("<#", cursor):
                depth += 1
                cursor += 2
            elif depth:
                if source.startswith("#>", cursor):
                    depth -= 1
                    cursor += 2
                else:
                    cursor += 1
            elif source[cursor] == "#":
                end = source.find("\n", cursor)
                end = len(source) if end < 0 else end
                if not source[source.rfind("\n", 0, cursor) + 1:cursor].strip() \
                        and re.match(r"#requires\s+-modules\b", source[cursor:end], re.I):
                    self.note_import(-1, -1)
                cursor = end
            else:
                cursor += 1
        for block in self.blocks:
            self.control(block)

    def containers(self, position):
        return sorted(start for start, end in self.blocks.items() if start < position < end
                      and self.bare[start - 1:start] not in ("@", "$"))

    def control(self, block):
        """Recognize synchronous control bodies without mistaking callback arguments for them."""
        if block in self.callbacks:
            return "callback"
        head = self.bare[:block].rstrip()
        header = head.endswith(")")
        if header:
            depth = 0
            for index in range(len(head) - 1, -1, -1):
                depth += (head[index] == ")") - (head[index] == "(")
                if not depth:
                    head = head[:index].rstrip()
                    break
        match = re.search(r"\b(if|elseif|else|for|foreach|while|do|switch|try|catch|finally)"
                          r"(?:\s+-[\w]+)*(?:\s*\[[^\]]+\](?:\s*,\s*\[[^\]]+\])*)?$", head, re.I)
        match = match or re.search(r"\b(switch)\b[^;{}\n]*\s-File\s+[^;{}\n]+$", head, re.I)
        if match and (header or match[1].lower() in ("else", "do", "try", "catch", "finally") or re.search(r"\s-File\s", head, re.I)):
            kind = match[1].lower()
            before = head[:match.start()].rstrip()
            if kind in ("for", "foreach", "while", "do", "switch"):
                before = re.sub(rf"{LABEL}$", "", before).rstrip()
            if kind in ("elseif", "else", "catch", "finally"):
                previous = next((start for start, end in self.blocks.items() if end == len(before)), None)
                allowed = ("if", "elseif") if kind in ("elseif", "else") else ("try", "catch")
                if previous is not None and self.control(previous) in allowed:
                    self.statements.add(match.start())
                    return kind
            elif not before.endswith(("&", ".", "|")) and command_position(before):
                self.statements.add(match.start())
                return kind
        parents = self.containers(block)
        return "clause" if parents and self.control(parents[-1]) == "switch" else None

    def iteration_end(self, block, kind):
        if kind == "callback":
            return self.callbacks[block]
        end = self.blocks[block]
        condition = re.match(r"\s*(?:while|until)\s*\(", self.bare[end:], re.I) if kind == "do" else None
        if condition:
            depth = 0
            for index in range(end + condition.end() - 1, len(self.bare)):
                depth += (self.bare[index] == "(") - (self.bare[index] == ")")
                if not depth:
                    return index + 1
        return end

    def binding(self, name, position):
        if "/" in name or "\\" in name:
            return "external"
        result, imported = "external", False
        call_blocks = self.containers(position)
        bodies = {item.body: item for item in self.definitions if item.body is not None}
        controls = {block: self.control(block) for block in call_blocks}
        deferred = any(block in bodies or controls[block] is None and not re.search(r"[&.]\s*$", self.bare[:block])
                       for block in call_blocks)
        for item in sorted(self.definitions, key=lambda row: row.start if row.body is not None and row.body < position < row.end else row.end):
            scope, _, short = item.name.rpartition(":")
            if short not in (name.lower(), "*"):
                continue
            # Deferred or repeated blocks can run after an import elsewhere in the command.
            if item.kind == "imported" and (deferred or any(kind in ("for", "foreach", "while", "do", "switch", "callback")
                                                           and block < item.start < self.iteration_end(block, kind)
                                                           for block, kind in controls.items())):
                imported = True
                continue
            inside = item.body is not None and item.body < position < item.end
            if item.end > position and not inside:
                # Calls inside a function may run after later declarations have executed.
                if any(block in bodies or block in self.callbacks for block in call_blocks) and item.start > position:
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
                if block in self.callbacks:
                    uncertain = True
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
            if item.kind == "imported":
                imported = True
                continue
            if scope == "private" and any(block not in self.containers(item.start) for block in call_blocks):
                uncertain = True
            if item.kind == "unresolved":
                result = "unresolved"
            elif uncertain or item.kind == "ambiguous" or item.kind == "removed" and result in ("ambiguous", "unresolved"):
                result = "ambiguous"
            else:
                result = "external" if item.kind == "removed" else "function"
        return "imported" if imported else result

    def note_import(self, position, end):
        """Record possible session changes without reading or executing loaded code."""
        self.definitions.append(Definition("global:*", position, end, None, "imported"))

    def argument_spans(self, cursor, end=None):
        """Keep argument spans and literal block spans in the submitted source."""
        end = len(self.bare) if end is None else end
        while cursor < end:
            if self.bare[cursor] in ";|\n)]}":
                break
            if self.bare[cursor].isspace():
                cursor += 1
                continue
            start, groups, values = cursor, [], []
            while cursor < end:
                if cursor in self.strings:
                    cursor = self.strings[cursor]
                    continue
                char = self.bare[cursor]
                if not groups and (char.isspace() or char in ";|\n)]}"):
                    break
                if char == "{" and cursor in self.blocks:
                    if all(group == "(" for group in groups) and self.bare[cursor - 1:cursor] not in ("@", "$"):
                        values.append(cursor)
                    cursor = self.blocks[cursor]
                    continue
                if char in "([":
                    groups.append(char)
                elif char in ")]" and groups:
                    groups.pop()
                cursor += 1
            yield start, cursor, values

    def callback_arguments(self, program, tail, position):
        """Read callback roles and version requirements from outer arguments only."""
        cursor, blocks, parallel, pending, positional = len(self.source) - len(tail), [], False, None, True
        piped = re.search(r"(?<!\|)\|\s*(?:&\s*)?$", self.bare[:position]) is not None
        callbacks = ("process", "begin", "end", "remainingscripts") if program == "foreach-object" else ("filterscript",)
        switches = {"verbose", "debug", "whatif", "confirm", "asjob", "usenewrunspace"}
        items = []
        for first, last, values in self.argument_spans(cursor):
            cursor = last
            splat = re.fullmatch(r"@(\w+)", self.bare[first:last])
            if splat:
                for name, a, b, code, values in parameters(self.source, position, splat[1]):
                    items.append((name, a, b, values, code))
            elif re.match(r"@[\w:]", self.bare[first:last]):
                raise Refusal("스플랫 매개 변수의 범위를 실행 전에 확정할 수 없다.")
            else:
                items.append((None, first, last, values, None))
        def canonical(name):
            return next((option for option in (*callbacks, "inputobject", "parallel") if option.startswith(name)), name)

        explicit = {canonical(match[1].lower()) for name, a, b, _, code in items if code is None
                    and (match := re.match(r"-([a-z]+)(:|$)", self.bare[a:b], re.I))}
        seen = set()
        for name, _, _, _, code in items:
            if code is not None and name is not None:
                key = canonical(name)
                if key in seen or key in explicit:
                    raise Refusal("중복된 스플랫 매개 변수를 실행 전에 확정할 수 없다.")
                seen.add(key)
        for name, start, end, values, code in items:
            argument = self.bare[start:end]
            parameter = re.match(r"-([a-z]+)(:|$)", argument, re.I) if code is None else None
            if name is not None or parameter:
                name = name if name is not None else parameter[1].lower()
                parallel |= program == "foreach-object" and len(name) >= 2 and "parallel".startswith(name)
                pending = "input" if "inputobject".startswith(name) else any(option.startswith(name) for option in callbacks)
                if parameter is not None and parameter[2] != ":":
                    if name in {"vb", "db", "wi", "cf"} or any(option.startswith(name) for option in switches):
                        pending = None
                    continue
                if parameter is not None:
                    start += parameter.end()
            # Only literal block values belong to this argument, never blocks inside a nested command.
            value = code
            if value is None:
                value = self.bare[start:end]
                for block in reversed(values):
                    value = value[:block - start] + "0" + value[self.blocks[block] - start:]
            literal = values and re.fullmatch(r"[\s(@),0]*", value)
            # Pipeline binding can execute a ScriptBlock input value, but not an array of blocks.
            callback = positional if pending is None else pending
            if pending == "input":
                callback = piped and not re.search(r"[@,]", value)
            if callback and literal:
                blocks.extend(values)
                if code is not None:
                    self.splatted.update(values)
            if pending is None and argument.strip(",") and (program == "where-object" or not literal):
                positional = False
            next_arg = self.bare[end:].lstrip(" \t\r")
            if not argument.endswith(",") and not next_arg.startswith(","):
                pending = None
        return cursor, blocks, parallel

    def define(self, program, tail, position):
        """Update callable state; return true when separate alias inspection is unnecessary."""
        if program in ("foreach-object", "where-object"):
            end, blocks, parallel = self.callback_arguments(program, tail, position)
            if parallel:
                self.parallel.add(position)
            else:
                self.callbacks.update((block, end) for block in blocks)
            return True
        if program == "invoke-expression":
            self.note_import(position, len(self.source) - len(tail))
            return True
        writes = ("set-item", "new-item", "si", "ni")
        removes = ("remove-item", "ri", "rm", "rmdir", "rd", "del", "erase")
        clears = ("clear-item", "cli")
        if program not in (*writes, *removes, *clears):
            return False
        offset = len(self.source) - len(tail)
        body = next((start for start in sorted(self.blocks) if start >= offset
                     and not re.search(r"[;\n|&]", self.bare[offset:start])), None)
        header = tail[:body - offset] if body is not None else tail
        try:
            argv = bound_arguments(header, self.source[:position])
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
