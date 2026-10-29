"""Syntax-aware JS/TS implementations; exact saved source is never reformatted."""
from __future__ import annotations

import hashlib
import unicodedata
from bisect import bisect_right
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version

from semantic_search.languages import PARSER_PINS, extractor_for, is_declaration_file
from semantic_search.source import physical_lines

FUNCTION_TYPES = frozenset({"function_declaration", "generator_function_declaration",
                           "function_expression", "generator_function", "arrow_function",
                           "method_definition"})
CLASS_TYPES = frozenset({"class_declaration", "abstract_class_declaration", "class"})
TRANSPARENT_TYPES = frozenset({"parenthesized_expression", "as_expression", "satisfies_expression",
                              "non_null_expression", "type_assertion"})


def parser_for(path):
    """Use verified wheels and fail clearly instead of accepting a changed grammar."""
    grammar = extractor_for(path).grammar
    packages = ("tree-sitter", "tree-sitter-javascript" if grammar == "javascript"
                else "tree-sitter-typescript")
    for package in packages:
        try:
            installed = version(package)
        except PackageNotFoundError as error:
            raise ValueError("JS/TS support requires the pinned 'languages' extra") from error
        if installed != PARSER_PINS[package]:
            raise ValueError(f"JS/TS parser {package} requires {PARSER_PINS[package]}, found {installed}")
    from tree_sitter import Language, Parser
    if grammar == "javascript":
        import tree_sitter_javascript
        language = Language(tree_sitter_javascript.language())
    else:
        import tree_sitter_typescript
        language = Language(tree_sitter_typescript.language_tsx() if grammar == "tsx"
                            else tree_sitter_typescript.language_typescript())
    return Parser(language)


def _variance_repairs(tree, raw):
    """Pad only TS declaration type-parameter modifiers unsupported by the pin.

    The grammar still supplies the declaration/list boundaries. Direct commas
    delimit parameters, so commas in nested constraints cannot start a repair.
    The replacement tree must parse cleanly; executable bytes are never changed.
    """
    owners = {"interface_declaration", "type_alias_declaration", "class_declaration",
              "abstract_class_declaration", "class"}
    repairs = []
    pending = [tree.root_node]
    while pending:
        node = pending.pop()
        pending.extend(node.named_children)
        if (node.type != "type_parameters" or node.parent.type not in owners
                or node.parent.child_by_field_name("type_parameters") != node):
            continue
        leaves = []
        stack = [node]
        while stack:
            item = stack.pop()
            if item.type == "comment":
                continue
            if item.children:
                stack.extend(reversed(item.children))
            elif item.end_byte > item.start_byte:
                leaves.append(item)
        boundaries = [child for child in node.children if child.type in {"<", ",", ">"}]
        for begin, end in zip(boundaries, boundaries[1:]):
            tokens = [item for item in leaves if begin.end_byte <= item.start_byte
                      and item.end_byte <= end.start_byte]
            if len(tokens) == 1 and node_text(tokens[0], raw) == "in":
                raise SyntaxError("Type parameter variance requires an identifier")
            if len(tokens) < 2 or node_text(tokens[0], raw) not in {"in", "out"}:
                continue
            # A plain parameter named `out` remains valid. Only an adjacent
            # identifier token makes the first contextual keyword a modifier.
            if tokens[1].type not in {"identifier", "type_identifier"}:
                continue
            modifiers = [tokens[0]]
            if (len(tokens) > 2 and node_text(tokens[1], raw) in {"in", "out"}
                    and tokens[2].type in {"identifier", "type_identifier"}):
                if [node_text(item, raw) for item in tokens[:2]] != ["in", "out"]:
                    raise SyntaxError("Type parameter variance permits only in, out, or in out")
                modifiers.append(tokens[1])
            name = tokens[len(modifiers)]
            if node_text(name, raw) == "in":
                raise SyntaxError("Type parameter variance requires an identifier")
            for modifier, following in zip(modifiers, tokens[1:]):
                gap = raw[modifier.end_byte:following.start_byte].decode("utf-8")
                if any(char in gap for char in "\r\n\u2028\u2029"):
                    raise SyntaxError("Type parameter variance cannot cross a line terminator")
                repairs.append((modifier.start_byte, modifier.end_byte))
    return repairs


def parse_source(path, source):
    parser = parser_for(path)
    raw = source.encode("utf-8")
    parsed_raw = raw
    tree = parser.parse(parsed_raw)
    if extractor_for(path).language == "typescript":
        repairs = _variance_repairs(tree, raw)
        if repairs:
            parsed = bytearray(raw)
            for start, end in repairs:
                parsed[start:end] = b" " * (end - start)
            parsed_raw = bytes(parsed)
            tree = parser.parse(parsed_raw)
    if not tree.root_node.has_error:
        # The pinned grammars can parse `export\nconst/function/...` as an
        # identifier followed by an unexported declaration. ECMAScript permits
        # that whitespace. Repair only AST-confirmed standalone export tokens;
        # equal-length ASCII padding keeps every original source byte offset.
        repairs = []
        pending = [tree.root_node]
        exportable = {"lexical_declaration", "variable_declaration", "function_declaration", "function_signature",
                      "generator_function_declaration", "class_declaration", "abstract_class_declaration",
                      "interface_declaration", "type_alias_declaration", "enum_declaration", "internal_module"}

        def allowed_export_container(container):
            if container is not None and container.type == "program":
                return True
            if (extractor_for(path).language == "typescript" and container is not None
                    and container.type == "statement_block" and container.parent.type == "internal_module"):
                owner = container.parent.parent
                while owner is not None and owner.type in {"expression_statement", "export_statement", "ambient_declaration"}:
                    owner = owner.parent
                return allowed_export_container(owner)
            return False

        while pending:
            node = pending.pop()
            if node.type == "export_statement" and not allowed_export_container(node.parent):
                raise SyntaxError(f"Export outside module scope in {path} at line {SourceLines(source).first(node)}")
            meaningful = [child for child in node.named_children if child.type != "comment"]
            if (node.type == "expression_statement" and len(meaningful) == 1
                    and meaningful[0].type == "identifier"
                    and node_text(meaningful[0], raw) == "export"):
                token = meaningful[0]
                following = node.next_named_sibling
                comments = [child for child in node.named_children if child.type == "comment"]
                while following is not None and following.type == "comment":
                    comments.append(following)
                    following = following.next_named_sibling
                gap = bytearray(raw[token.end_byte:following.start_byte]) if following is not None else bytearray()
                for comment in comments:
                    begin, end = comment.start_byte - token.end_byte, comment.end_byte - token.end_byte
                    gap[begin:end] = b" " * (end - begin)
                if (following is None or following.type not in exportable or not gap
                        or not allowed_export_container(node.parent)
                        or any(char not in " \t\v\f\r\n\u00a0\ufeff\u2028\u2029" and unicodedata.category(char) != "Zs"
                               for char in gap.decode("utf-8"))):
                    raise SyntaxError(f"Invalid standalone export in {path} at line {SourceLines(source).first(node)}")
                repairs.append((token.end_byte, following.start_byte))
            pending.extend(node.named_children)
        if repairs:
            parsed = bytearray(parsed_raw)
            for start, end in repairs:
                parsed[start:end] = b" " * (end - start)
            tree = parser.parse(bytes(parsed))
    if tree.root_node.has_error:
        # Includes both explicit ERROR nodes and inserted MISSING nodes.
        pending = [tree.root_node]
        first = tree.root_node
        while pending:
            item = pending.pop()
            if item.is_error or item.is_missing:
                first = item
                break
            pending.extend(reversed(item.children))
        raise SyntaxError(f"Tree-sitter parse error in {path} at line {SourceLines(source).first(first)}")
    return tree


def node_text(node, raw):
    return raw[node.start_byte:node.end_byte].decode("utf-8") if node is not None else ""


def static_name(node, raw):
    if node is None:
        return None
    if node.type in {"identifier", "property_identifier", "private_property_identifier",
                     "type_identifier", "shorthand_property_identifier_pattern"}:
        return node_text(node, raw)
    if node.type == "string":
        value = node_text(node, raw)[1:-1]
        return value if value and "\\" not in value else None
    if node.type == "computed_property_name" and len(node.named_children) == 1:
        child = node.named_children[0]
        return static_name(child, raw) if child.type == "string" else None
    if node.type == "member_expression":
        owner = static_name(node.child_by_field_name("object"), raw)
        prop = static_name(node.child_by_field_name("property"), raw)
        return f"{owner}.{prop}" if owner and prop else None
    if node.type == "subscript_expression":
        owner = static_name(node.child_by_field_name("object"), raw)
        index = node.child_by_field_name("index")
        prop = static_name(index, raw) if index and index.type == "string" else None
        return f"{owner}.{prop}" if owner and prop else None
    return None


class SourceLines:
    """Map UTF-8 byte offsets to physical rows, including bare CR line endings."""

    def __init__(self, source):
        self.starts = [0]
        for line in physical_lines(source):
            self.starts.append(self.starts[-1] + len(line.encode("utf-8")))

    def first(self, node):
        return bisect_right(self.starts, node.start_byte)

    def last(self, node):
        return bisect_right(self.starts, max(node.start_byte, node.end_byte - 1))

    def marker(self, node):
        row = self.first(node)
        return f"{row}:{node.start_byte - self.starts[row - 1] + 1}"


def unwrap_expression(node):
    while node is not None and node.type in TRANSPARENT_TYPES:
        candidates = [child for child in node.named_children if child.type not in
                      {"comment", "type_arguments", "type_identifier", "type_annotation", "predefined_type"}]
        if not candidates:
            break
        node = candidates[0]
    return node


def _body_lines(body, locations):
    if body is None:
        return set()
    if body.type != "statement_block":
        return set(range(locations.first(body), locations.last(body) + 1))
    lines = set()
    for statement in body.named_children:
        if statement.type in {"comment", "empty_statement"}:
            continue
        # Directive-only bodies have no implementation.
        if (statement.type == "expression_statement" and statement.named_children
                and statement.named_children[0].type == "string"):
            continue
        lines.update(range(locations.first(statement), locations.last(statement) + 1))
    return lines


def _runtime_lines(node, locations):
    """Executed initializer expressions outside bodies already indexed as functions."""
    if node is None or node.type in FUNCTION_TYPES or node.type in CLASS_TYPES:
        return set()
    if node.type in {"comment", "type_annotation", "type_identifier", "predefined_type"}:
        return set()
    if node.type == "expression_statement":
        return {line for child in node.named_children for line in _runtime_lines(child, locations)}
    if node.type in TRANSPARENT_TYPES:
        return _runtime_lines(node.named_children[0], locations) if node.named_children else set()
    if node.type == "assignment_expression":
        value = node.child_by_field_name("right")
        return _runtime_lines(value, locations)
    if node.type in {"pair", "public_field_definition", "field_definition"}:
        key = node.child_by_field_name("key") or node.child_by_field_name("name") or node.child_by_field_name("property")
        key_lines = _runtime_lines(key, locations) if key is not None and key.type == "computed_property_name" else set()
        return key_lines | _runtime_lines(node.child_by_field_name("value"), locations)
    lines = set()
    if node.type not in {"object", "array", "arguments"}:
        lines.update((locations.first(node), locations.last(node)))
    for child in node.named_children:
        lines.update(_runtime_lines(child, locations))
    return lines


@dataclass(frozen=True)
class Definition:
    symbol: str
    node: object
    span: object
    body: object
    kind: str = "function"
    start_line: int | None = None
    end_line: int | None = None
    implementation_lines: frozenset[int] = frozenset()


def definitions(path, source, tree=None):
    """Stable names come from declarations, assignments, or a named factory call.

    Anonymous unbound callbacks are traversed but not indexed as named functions.
    Runtime initializers use explicit module/field/static labels. Type-only nodes
    and function declarations without a body never provide implementations.
    """
    tree = tree or parse_source(path, source)
    if is_declaration_file(path):
        return []
    raw = source.encode("utf-8")
    locations = SourceLines(source)
    output = []

    def start_with_decorators(node):
        start = locations.first(node)
        sibling = node.prev_named_sibling
        while sibling is not None and sibling.type == "decorator":
            start = locations.first(sibling)
            sibling = sibling.prev_named_sibling
        return start

    def add(symbol, node, span, body, kind="function", implementation=None):
        implementation = _body_lines(body, locations) if implementation is None else implementation
        if implementation:
            output.append(Definition(symbol, node, span, body, kind,
                                     start_with_decorators(span), locations.last(span),
                                     frozenset(implementation)))

    def add_field_groups(body, prefix):
        """Context for adjacent initializers, separate for static/instance runs.

        Methods, uninitialized/declare fields, function-valued fields and static
        blocks end a run. Individual field definitions remain independently
        indexed; these additional windows are explicitly initialization context.
        """
        counts = {"static": 0, "instance": 0}
        run, mode = [], None

        def flush():
            if len(run) < 2:
                return
            counts[mode] += 1
            implementation = {line for field in run for line in _runtime_lines(field, locations)}
            symbol = ".".join((*prefix, f"<{mode}-initializers:{counts[mode]}>"))
            # The class_body is not an individual binding/call owner. Using it
            # prevents the context group from overwriting a field's call graph.
            output.append(Definition(symbol, body, body, None, "initializer_group",
                                     start_with_decorators(run[0]), locations.last(run[-1]),
                                     frozenset(implementation)))

        for field in body.named_children:
            if field.type == "comment":
                continue
            value = field.child_by_field_name("value")
            eligible = (field.type in {"public_field_definition", "field_definition"}
                        and value is not None
                        and unwrap_expression(value).type not in FUNCTION_TYPES | CLASS_TYPES
                        and not any(child.type == "declare" for child in field.children)
                        and _runtime_lines(field, locations))
            current = "static" if any(child.type == "static" for child in field.children) else "instance"
            if not eligible or mode != current:
                flush()
                run = []
            if eligible:
                run.append(field)
                mode = current
            else:
                mode = None
        flush()

    def walk(node, prefix=(), bound=None, span=None, in_function=False):
        kind = node.type
        if kind in {"ambient_declaration", "interface_declaration", "type_alias_declaration"}:
            return
        if kind == "export_statement":
            default_export = "default" in [c.type for c in node.children]
            for child in node.named_children:
                before = len(output)
                walk(child, prefix, "default" if default_export else None, node, in_function)
                if (default_export and child == node.child_by_field_name("value")
                        and child.type not in FUNCTION_TYPES | CLASS_TYPES | {"function_signature"}):
                    covered = {line for item in output[before:] for line in range(item.start_line, item.end_line + 1)}
                    add(".".join((*prefix, "<module>", "default")), child, node, child,
                        "initializer", _runtime_lines(child, locations) - covered)
            return
        if kind in TRANSPARENT_TYPES:
            for child in node.named_children:
                if not child.type.endswith("type"):
                    walk(child, prefix, bound, span or node, in_function)
            return
        if kind in {"lexical_declaration", "variable_declaration"}:
            for child in node.named_children:
                walk(child, prefix, span=span, in_function=in_function)
            return
        if kind == "enum_declaration":
            # const enums are type-time declarations under normal TS emission.
            if not in_function and not any(child.type == "const" for child in node.children):
                name = static_name(node.child_by_field_name("name"), raw)
                if name:
                    add(".".join((*prefix, "<module>", name)), node, span or node,
                        node.child_by_field_name("body"), "initializer")
            return
        if kind in FUNCTION_TYPES:
            body = node.child_by_field_name("body")
            name = static_name(node.child_by_field_name("name"), raw)
            name = name if kind.endswith("declaration") or kind == "method_definition" else bound or name
            if kind == "method_definition" and name:
                accessor = next((child.type for child in node.children if child.type in {"get", "set"}), None)
                if accessor:
                    name = f"{accessor} {name}"
            qualified = (*prefix, name or f"<callback@{locations.marker(node)}>")
            if name:
                add(".".join(qualified), node, span or node, body)
            if body is not None:
                walk(body, qualified, in_function=True)
            return
        if kind in CLASS_TYPES or kind == "internal_module":
            name = static_name(node.child_by_field_name("name"), raw) or bound
            qualified = (*prefix, name or f"<class@{locations.marker(node)}>")
            body = node.child_by_field_name("body")
            if body is not None:
                walk(body, qualified, in_function=in_function)
                if kind in CLASS_TYPES:
                    add_field_groups(body, qualified)
            return
        if kind in {"variable_declarator", "public_field_definition", "field_definition", "pair"}:
            name_node = node.child_by_field_name("key" if kind == "pair" else "name")
            if kind == "field_definition":
                name_node = node.child_by_field_name("property") or name_node
            name = static_name(name_node, raw)
            value = node.child_by_field_name("value")
            if value is not None:
                wrapper = span or node
                # Include const/let/var when there is exactly one binding.
                if (kind == "variable_declarator" and node.parent.type in {"lexical_declaration", "variable_declaration"}
                        and len([c for c in node.parent.named_children if c.type == kind]) == 1):
                    wrapper = span or node.parent
                before = len(output)
                walk(value, prefix, name, wrapper, in_function)
                if (kind != "pair"
                        and (kind != "variable_declarator" or not in_function)
                        and value.type not in FUNCTION_TYPES and value.type not in CLASS_TYPES):
                    name = name or f"<field@{locations.marker(node)}>"
                    label = ".".join((*prefix, name)) if kind != "variable_declarator" else ".".join((*prefix, "<module>", name))
                    covered = {line for item in output[before:] for line in range(item.start_line, item.end_line + 1)}
                    runtime = _runtime_lines(node, locations) if kind != "variable_declarator" else _runtime_lines(value, locations)
                    add(label, node, wrapper, value, "initializer", runtime - covered)
            return
        if kind == "assignment_expression":
            value = node.child_by_field_name("right")
            if value is not None:
                walk(value, prefix, static_name(node.child_by_field_name("left"), raw), span or node, in_function)
            return
        if kind == "object":
            for child in node.named_children:
                walk(child, (*prefix, bound) if bound else prefix, in_function=in_function)
            return
        if kind == "call_expression" and bound:
            args = node.child_by_field_name("arguments")
            callbacks = [child for child in args.named_children if unwrap_expression(child).type in FUNCTION_TYPES] if args else []
            for index, child in enumerate(callbacks, 1):
                callback_name = bound if len(callbacks) == 1 else f"{bound}.callback{index}"
                walk(child, prefix, callback_name, span or node, in_function)
            for child in node.named_children:
                if child != args:
                    walk(child, prefix, in_function=in_function)
                elif args:
                    for arg in args.named_children:
                        if arg not in callbacks:
                            walk(arg, prefix, in_function=in_function)
            return
        if kind == "class_static_block":
            body = node.child_by_field_name("body")
            # Multiple static blocks have distinct, deterministic names.
            ordinal = 1 + sum(c.type == kind for c in node.parent.named_children if c.start_byte < node.start_byte)
            add(".".join((*prefix, f"<static:{ordinal}>")), node, node, body, "initializer")
            if body is not None:
                walk(body, prefix, in_function=True)
            return
        if (kind in {"statement_block", "switch_body"} and node.parent is not None
                and node.parent.type not in FUNCTION_TYPES | CLASS_TYPES | {"internal_module", "class_static_block"}):
            prefix = (*prefix, f"<block@{locations.marker(node)}>")
        # Top-level effect statements receive honest module labels. Definitions
        # discovered within them remain separate; no import/type prose is indexed.
        before = len(output)
        for child in node.named_children:
            walk(child, prefix, in_function=in_function)
        if (not in_function and node.parent is not None and node.parent.type == "program"
                and kind in {"expression_statement", "if_statement", "for_statement", "for_in_statement",
                             "while_statement", "do_statement", "try_statement", "switch_statement"}
                and not (kind == "expression_statement" and node.named_children
                         and node.named_children[0].type == "assignment_expression"
                         and node.named_children[0].child_by_field_name("right").type in FUNCTION_TYPES)):
            covered = {line for item in output[before:] for line in range(item.start_line, item.end_line + 1)}
            add("<module>", node, node, node, "initializer", _runtime_lines(node, locations) - covered)

    walk(tree.root_node)
    return output


def extract_javascript(path, source, max_lines, overlap_lines):
    from semantic_search.corpus import Snippet
    lines = physical_lines(source)
    output = []
    seen_ids = set()
    for definition in definitions(path, source):
        implementation = definition.implementation_lines
        start = definition.start_line
        final = definition.end_line
        while start <= final:
            end = min(start + max_lines - 1, final)
            if any(start <= line <= end for line in implementation):
                text = "".join(lines[start - 1:end])
                identity = f"{path}:{definition.symbol}:{start}-{end}"
                # JS permits repeated declarations/assignments on one line.
                if identity in seen_ids:
                    identity += f":definition@{definition.node.start_byte}"
                seen_ids.add(identity)
                output.append(Snippet(identity, path,
                                      definition.symbol, start, end, text,
                                      hashlib.sha256(text.encode("utf-8")).hexdigest()))
            if end == final:
                break
            start += max_lines - overlap_lines
    return output
