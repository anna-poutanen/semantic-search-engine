"""Explicit lexical JS/TS calls and relative ES imports, without runtime dispatch."""
from __future__ import annotations

import posixpath
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from semantic_search.javascript import (CLASS_TYPES, FUNCTION_TYPES,
                                        definitions, node_text, parse_source, static_name,
                                        unwrap_expression)
from semantic_search.languages import EXTRACTORS


@dataclass
class Scope:
    parent: object = None
    kind: str = "block"
    dynamic: bool = False
    bindings: dict = field(default_factory=lambda: defaultdict(list))


def _unwrap(node):
    return unwrap_expression(node)


def _pattern_names(node, raw):
    if node is None:
        return []
    if node.type in {"identifier", "shorthand_property_identifier_pattern"}:
        return [node_text(node, raw)]
    if node.type in {"required_parameter", "optional_parameter", "pair_pattern"}:
        return _pattern_names(node.child_by_field_name("pattern") or node.child_by_field_name("value"), raw)
    if node.type in {"assignment_pattern", "object_assignment_pattern"}:
        return _pattern_names(node.child_by_field_name("left"), raw)
    if node.type in {"type_annotation", "type_identifier", "property_identifier", "decorator"}:
        return []
    return [name for child in node.named_children for name in _pattern_names(child, raw)]


def _relative_module(path, specifier, sources):
    if not specifier.startswith(".") or "\\" in specifier:
        return None
    base = posixpath.normpath(posixpath.join(posixpath.dirname(path), specifier))
    if base.startswith("../") or base == "..":
        return None
    if base in sources:
        return base
    extensions = [ext for ext in EXTRACTORS if ext != ".py"]
    suffix = PurePosixPath(base).suffix
    candidates = [base + ext for ext in extensions] + [base + "/index" + ext for ext in extensions]
    # TS source routinely uses emitted .js/.mjs/.cjs import spellings.
    if suffix in {".js", ".mjs", ".cjs"}:
        stem = str(PurePosixPath(base).with_suffix(""))
        candidates += [stem + ext for ext in {".js": (".ts", ".tsx"), ".mjs": (".mts",), ".cjs": (".cts",)}[suffix]]
    matches = [candidate for candidate in candidates if candidate in sources]
    return matches[0] if len(matches) == 1 else None


def _require_import(node, raw, path, sources):
    """Recognize only a literal relative require, optionally followed by one property."""
    node = _unwrap(node)
    exported = "*"
    if node is not None and node.type == "member_expression":
        exported = static_name(node.child_by_field_name("property"), raw)
        node = _unwrap(node.child_by_field_name("object"))
    if node is None or node.type != "call_expression" or static_name(node.child_by_field_name("function"), raw) != "require":
        return None
    args = node.child_by_field_name("arguments")
    if args is None or len(args.named_children) != 1 or args.named_children[0].type != "string":
        return None
    target = _relative_module(path, static_name(args.named_children[0], raw) or "", sources)
    return (target, exported) if target and exported else None


def _exports_escape(node, raw):
    if node is None:
        return False
    reference = static_name(node, raw)
    if reference in {"exports", "module", "module.exports"}:
        return True
    # Reading an exported property does not itself alias the export object.
    if node.type in {"member_expression", "subscript_expression"}:
        return any(_exports_escape(child, raw) for child in node.named_children
                   if child != node.child_by_field_name("object"))
    return any(_exports_escape(child, raw) for child in node.named_children)


def reference_edges(sources, windows):
    parsed = {path: (source.encode("utf-8"), parse_source(path, source)) for path, source in sources.items()}
    records = {path: definitions(path, sources[path], tree) for path, (_, tree) in parsed.items()}
    indexed = {path: {item.node.id: item for item in items if (path, item.symbol) in windows}
               for path, items in records.items()}
    exports = defaultdict(dict)
    commonjs_exports = defaultdict(dict)
    contexts = {}

    for path, (raw, tree) in parsed.items():
        module_scope = Scope(kind="module")
        scopes, calls, mutations, export_writes = [], [], [], []
        unsafe_commonjs = False

        def bind(scope, name, target=None, at=0, namespace=None):
            if name:
                scope.bindings[name].append((target, at, namespace))

        def visit(node, scope, owner=None):
            nonlocal unsafe_commonjs
            kind = node.type
            record = indexed[path].get(node.id)
            if record is not None and record.kind == "initializer":
                owner = (path, record.symbol)
            if kind in {"ambient_declaration", "interface_declaration", "type_alias_declaration"}:
                return
            if kind in FUNCTION_TYPES:
                record = indexed[path].get(node.id)
                name = static_name(node.child_by_field_name("name"), raw)
                if kind.endswith("declaration"):
                    bind(scope, name, (path, record.symbol) if record else None)
                local = Scope(scope, "function")
                scopes.append(local)
                for param in _pattern_names(node.child_by_field_name("parameters") or node.child_by_field_name("parameter"), raw):
                    bind(local, param)
                # A named expression has its own lexical self-binding.
                if kind in {"function_expression", "generator_function"}:
                    bind(local, name, (path, record.symbol) if record else None)
                body = node.child_by_field_name("body")
                if body is not None:
                    visit(body, local, (path, record.symbol) if record else None)
                return
            if kind in CLASS_TYPES or kind == "internal_module":
                bind(scope, static_name(node.child_by_field_name("name"), raw))
                local = Scope(scope)
                scopes.append(local)
                body = node.child_by_field_name("body")
                if body is not None:
                    visit(body, local)
                return
            if kind == "enum_declaration":
                bind(scope, static_name(node.child_by_field_name("name"), raw))
            if kind == "with_statement":
                body = node.child_by_field_name("body")
                for child in node.named_children:
                    if child == body:
                        local = Scope(scope, dynamic=True)
                        scopes.append(local)
                        visit(child, local, owner)
                    else:
                        visit(child, scope, owner)
                return
            if kind in {"statement_block", "switch_body", "catch_clause", "for_statement", "for_in_statement"}:
                scope = Scope(scope)
                scopes.append(scope)
                if kind == "catch_clause":
                    for name in _pattern_names(node.child_by_field_name("parameter"), raw):
                        bind(scope, name)
                elif kind == "for_in_statement":
                    for name in _pattern_names(node.child_by_field_name("left"), raw):
                        bind(scope, name)
            if kind == "import_statement":
                specifier = static_name(node.child_by_field_name("source"), raw)
                target_path = _relative_module(path, specifier or "", sources)
                type_only = any(child.type == "type" for child in node.children)
                for clause in node.named_children:
                    if clause.type != "import_clause":
                        continue
                    for item in clause.named_children:
                        if item.type == "identifier":
                            bind(scope, node_text(item, raw), namespace=(target_path, "default") if target_path and not type_only else None)
                        elif item.type == "namespace_import":
                            identifier = next((c for c in item.named_children if c.type == "identifier"), None)
                            bind(scope, node_text(identifier, raw), namespace=(target_path, "*") if target_path and not type_only else None)
                        elif item.type == "named_imports":
                            for spec in item.named_children:
                                name = static_name(spec.child_by_field_name("name"), raw)
                                alias = static_name(spec.child_by_field_name("alias"), raw) or name
                                is_type = type_only or any(c.type == "type" for c in spec.children)
                                bind(scope, alias, namespace=(target_path, name) if target_path and not is_type else None)
                return
            if kind == "variable_declarator":
                value = _unwrap(node.child_by_field_name("value"))
                unsafe_commonjs |= _exports_escape(value, raw)
                record = indexed[path].get(value.id) if value is not None and value.type in FUNCTION_TYPES else None
                binding_scope = scope
                if node.parent.type == "variable_declaration":
                    while binding_scope.kind not in {"module", "function"} and binding_scope.parent is not None:
                        binding_scope = binding_scope.parent
                pattern = node.child_by_field_name("name")
                imported = _require_import(value, raw, path, sources)
                at = 0 if binding_scope.kind == "module" else node.start_byte
                if imported and pattern is not None and pattern.type == "identifier":
                    bind(binding_scope, node_text(pattern, raw), at=at,
                         namespace=(*imported, "commonjs", scope))
                elif imported and imported[1] == "*" and pattern is not None and pattern.type == "object_pattern":
                    for item in pattern.named_children:
                        if item.type == "shorthand_property_identifier_pattern":
                            name = node_text(item, raw)
                            bind(binding_scope, name, at=at, namespace=(imported[0], name, "commonjs", scope))
                        elif item.type == "pair_pattern" and item.child_by_field_name("value").type == "identifier":
                            bind(binding_scope, node_text(item.child_by_field_name("value"), raw), at=at,
                                 namespace=(imported[0], static_name(item.child_by_field_name("key"), raw), "commonjs", scope))
                        else:
                            for name in _pattern_names(item, raw):
                                bind(binding_scope, name)
                else:
                    for name in _pattern_names(pattern, raw):
                        bind(binding_scope, name, (path, record.symbol) if record else None, at)
            if (kind in {"assignment_expression", "augmented_assignment_expression", "update_expression"}
                    or (kind == "unary_expression" and any(c.type == "delete" for c in node.children))):
                target = node.child_by_field_name("left") or node.child_by_field_name("argument")
                reference = static_name(target, raw)
                unsafe_commonjs |= _exports_escape(node.child_by_field_name("right"), raw)
                if reference and (reference == "module.exports" or reference.startswith(("exports.", "module.exports."))):
                    direct = (node.parent is not None and node.parent.type == "expression_statement"
                              and node.parent.parent is not None and node.parent.parent.type == "program"
                              and kind == "assignment_expression")
                    export_writes.append((reference, node.child_by_field_name("right"), direct))
                elif reference in {"exports", "module"}:
                    unsafe_commonjs = True
                elif target is not None and target.type == "subscript_expression":
                    base = static_name(target.child_by_field_name("object"), raw)
                    if base in {"exports", "module.exports"}:
                        unsafe_commonjs = True
                for name in _pattern_names(target, raw):
                    mutations.append((scope, name, target.type == "identifier"))
                if reference and "." in reference:
                    mutations.append((scope, reference.split(".")[0], False))
            if kind == "call_expression":
                reference = static_name(node.child_by_field_name("function"), raw)
                if reference == "eval":
                    dynamic_scope = scope
                    while dynamic_scope.kind not in {"module", "function"} and dynamic_scope.parent is not None:
                        dynamic_scope = dynamic_scope.parent
                    dynamic_scope.dynamic = True
                if owner and reference:
                    calls.append((owner, reference, scope, node.start_byte))
                args = node.child_by_field_name("arguments")
                if _exports_escape(args, raw):
                    unsafe_commonjs = True
            if kind in {"return_statement", "yield_expression", "throw_statement"}:
                unsafe_commonjs |= any(_exports_escape(child, raw) for child in node.named_children)
            for child in node.named_children:
                visit(child, scope, owner)

        scopes.append(module_scope)
        visit(tree.root_node, module_scope)
        # Mutated bindings (including imported namespaces) are never resolved.
        for scope, name, free_write in mutations:
            while scope is not None:
                if name in scope.bindings:
                    scope.bindings[name].append((None, 0, None))
                    break
                scope = scope.parent
            else:
                # An assignment to a free name makes that global unresolved.
                if free_write:
                    module_scope.bindings[name].append((None, 0, None))

        # Build only direct exports. Re-exports and factory products are unresolved.
        def exported_local(name):
            entries = module_scope.bindings.get(name, ())
            return entries[0][0] if len(entries) == 1 and entries[0][2] is None else None

        for statement in tree.root_node.named_children:
            if statement.type != "export_statement" or statement.child_by_field_name("source") is not None:
                continue
            declaration = statement.child_by_field_name("declaration") or statement.child_by_field_name("value")
            is_default = any(c.type == "default" for c in statement.children)
            if is_default:
                unwrapped = _unwrap(declaration)
                record = indexed[path].get(unwrapped.id) if unwrapped is not None and unwrapped.type in FUNCTION_TYPES else None
                if unwrapped is not None and unwrapped.type.endswith("declaration"):
                    target = exported_local(static_name(unwrapped.child_by_field_name("name"), raw))
                else:
                    target = (path, record.symbol) if record else exported_local(static_name(unwrapped, raw))
                if target:
                    exports[path]["default"] = target
            elif declaration is not None:
                if declaration.type in FUNCTION_TYPES:
                    name = static_name(declaration.child_by_field_name("name"), raw)
                    target = exported_local(name)
                    if target:
                        exports[path][name] = target
                elif declaration.type in {"lexical_declaration", "variable_declaration"}:
                    for item in declaration.named_children:
                        name = static_name(item.child_by_field_name("name"), raw)
                        target = exported_local(name)
                        if target:
                            exports[path][name] = target
            else:
                for clause in statement.named_children:
                    if clause.type == "export_clause":
                        for spec in clause.named_children:
                            if any(c.type == "type" for c in spec.children):
                                continue
                            name = static_name(spec.child_by_field_name("name"), raw)
                            alias = static_name(spec.child_by_field_name("alias"), raw) or name
                            target = exported_local(name)
                            if target:
                                exports[path][alias] = target

        # Only unambiguous, top-level CommonJS export writes are supported.
        # Replacing module.exports together with writes through the exports alias
        # is deliberately unresolved, as are reflection and shadowed globals.
        unsafe_commonjs |= "exports" in module_scope.bindings or "module" in module_scope.bindings
        whole = [item for item in export_writes if item[0] == "module.exports"]
        if not unsafe_commonjs and (not whole or (len(whole) == 1 and len(export_writes) == 1)):
            candidates = defaultdict(list)

            def exported_value(value):
                value = _unwrap(value)
                record = indexed[path].get(value.id) if value is not None and value.type in FUNCTION_TYPES else None
                return (path, record.symbol) if record else exported_local(static_name(value, raw))

            for reference, value, direct in export_writes:
                if reference == "module.exports":
                    if direct and value is not None and value.type == "object":
                        for item in value.named_children:
                            if item.type == "pair":
                                name = static_name(item.child_by_field_name("key"), raw)
                                candidates[name].append(exported_value(item.child_by_field_name("value")))
                            elif item.type == "method_definition":
                                name = static_name(item.child_by_field_name("name"), raw)
                                candidates[name].append(exported_value(item))
                            elif item.type == "shorthand_property_identifier":
                                name = node_text(item, raw)
                                candidates[name].append(exported_local(name))
                            else:
                                unsafe_commonjs = True
                    else:
                        candidates["<callable>"].append(exported_value(value) if direct else None)
                else:
                    name = reference.removeprefix("module.").removeprefix("exports.")
                    candidates[name].append(exported_value(value) if direct and "." not in name else None)
            if not unsafe_commonjs:
                commonjs_exports[path] = {name: values[0] for name, values in candidates.items()
                                           if name and len(values) == 1 and values[0] is not None}
        contexts[path] = calls

    edges = defaultdict(list)
    for calls in contexts.values():
        for owner, reference, scope, at in calls:
            ancestor = scope
            while ancestor is not None and not ancestor.dynamic:
                ancestor = ancestor.parent
            if ancestor is not None:
                continue
            head, *tail = reference.split(".")
            while scope is not None and head not in scope.bindings:
                scope = scope.parent
            if scope is None:
                continue
            entries = scope.bindings[head]
            if len(entries) != 1:
                continue
            target, defined_at, namespace = entries[0]
            if defined_at > at:
                continue
            if namespace:
                target_path, exported, *mode = namespace
                export_map = exports
                if mode:
                    require_scope = mode[1]
                    while require_scope is not None and "require" not in require_scope.bindings:
                        require_scope = require_scope.parent
                    if require_scope is not None:
                        continue
                    export_map = commonjs_exports
                if exported == "*":
                    target = export_map[target_path].get(tail[0]) if len(tail) == 1 else export_map[target_path].get("<callable>") if not tail and mode else None
                elif not tail:
                    target = export_map[target_path].get(exported)
                else:
                    target = None
            elif tail:
                target = None
            if target in windows and target != owner and target not in edges[owner]:
                edges[owner].append(target)
    return edges
