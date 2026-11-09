"""Research-only dependencies declared by explicit Python parameter annotations.

This does not change ReferenceGraph or prove runtime dispatch. An annotation is
a source relationship, not a runtime type check: external mutation, subclasses,
reflection and arbitrary callees can still change behavior. Only direct calls on
an unmodified, non-escaping parameter and unambiguous source class are admitted.
All inputs are captured source values; this module never reads or executes them.
"""
from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Mapping
from copy import deepcopy
import hashlib
from pathlib import PurePosixPath

from semantic_search.corpus import Snippet
from semantic_search.source import physical_lines


SCHEMA = "annotated_parameter_dependencies_v1"
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda,
           ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_DYNAMIC = {"getattr", "setattr", "delattr", "vars", "eval", "exec", "__setattr__", "__delattr__"}
_DISPATCH_HOOKS = {"__getattribute__", "__getattr__"}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _module(path):
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts[:1] == ["src"]:
        parts.pop(0)
    if parts[-1:] == ["__init__"]:
        parts.pop()
    return ".".join(parts)


def _dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _dotted(node.value)
        return f"{owner}.{node.attr}" if owner else None
    return None


def _scope_nodes(node):
    """One scope, including nested declarations but not their evaluated bodies."""
    for child in ast.iter_child_nodes(node):
        yield child
        if not isinstance(child, _SCOPES):
            yield from _scope_nodes(child)


def _bound_names(node):
    """All syntactic bindings in one scope, including unreachable later writes."""
    names = []
    for child in _scope_nodes(node):
        if isinstance(child, (ast.Name,)) and isinstance(child.ctx, (ast.Store, ast.Del)):
            names.append(child.id)
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(child.name)
        elif isinstance(child, (ast.Import, ast.ImportFrom)):
            names.extend(alias.asname or alias.name.split(".")[0] for alias in child.names)
        elif isinstance(child, ast.ExceptHandler) and child.name:
            names.append(child.name)
        elif isinstance(child, (ast.Global, ast.Nonlocal)):
            names.extend(child.names)
        elif isinstance(child, (ast.MatchAs, ast.MatchStar)) and child.name:
            names.append(child.name)
        elif isinstance(child, ast.MatchMapping) and child.rest:
            names.append(child.rest)
    return names


def _parameters(function):
    args = [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]
    return args + [arg for arg in (function.args.vararg, function.args.kwarg) if arg]


def _span(node):
    return min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", ()))]), node.end_lineno


def _witness(path, source, node):
    lines = physical_lines(source)
    prefix = [0]
    for line in lines:
        prefix.append(prefix[-1] + len(line.encode("utf-8")))
    start = prefix[node.lineno - 1] + node.col_offset
    end = prefix[node.end_lineno - 1] + node.end_col_offset
    literal = source.encode("utf-8")[start:end]
    return {"path": path, "start_line": node.lineno, "end_line": node.end_lineno,
            "start_byte": start, "end_byte": end, "source": literal.decode("utf-8"),
            "sha256": _sha(literal)}


def _annotation(node):
    if isinstance(node, ast.Constant) and type(node.value) is str:
        try:
            node = ast.parse(node.value, mode="eval").body
        except (SyntaxError, RecursionError, OverflowError, ValueError):
            return None
    return _dotted(node)


def _import_target(node, alias, module, path):
    if isinstance(node, ast.Import):
        # A direct alias binds exactly one module; bare dotted imports require
        # package attribute initialization semantics and are left unresolved.
        return ("module", alias.name) if alias.asname or "." not in alias.name else ("unknown", None)
    package = module.split(".") if PurePosixPath(path).name == "__init__.py" else module.split(".")[:-1]
    if node.level > len(package):
        return "unknown", None
    prefix = package[:len(package) - node.level + 1] if node.level else []
    return "export", ".".join([*prefix, *filter(None, (node.module or "").split(".")), alias.name])


class AnnotatedDependencies:
    """Build an additional source-only graph without touching production graphs.

    ``snippets`` are production Snippet dataclasses, not compact result rows.
    ``definition_keys`` maps their IDs to exact canonical Python definition spans.
    Invalid literal bindings fail; unresolved syntax/names decline relationships.
    The initial scope is top-level functions and direct methods of top-level
    classes. Nested lexical functions/classes, inheritance, class decorators,
    parameter aliases/escapes and unsupported annotations are not inferred.
    """

    def __init__(self, sources, snippets, *, definition_keys, cancel_check=lambda: None):
        if not isinstance(sources, Mapping) or not isinstance(definition_keys, Mapping):
            raise ValueError("Captured source and definition mappings required")
        if any(type(path) is not str or type(source) is not str for path, source in sources.items()):
            raise ValueError("Literal source paths and strings required")
        self._check = cancel_check
        self._sources = dict(sources)
        self._trees, self._modules, self._bindings = {}, defaultdict(list), {}
        self._definitions, self._classes, self._functions = {}, {}, []
        self._declined, self._windows = [], defaultdict(list)
        self._tainted_classes, self._tainted_methods = set(), set()
        self._tainted_modules, self._mutation_scan = set(), False
        self.edges = {}
        self._parse()
        self._validate_windows(list(snippets), definition_keys)
        self._module_bindings()
        self._class_mutations()
        accepted = self._calls()
        edges = defaultdict(set)
        for fact in accepted:
            edges[(fact["caller"]["path"], fact["caller"]["symbol"])].add(
                (fact["callee"]["path"], fact["callee"]["symbol"]))
        self.edges = {key: tuple(sorted(targets)) for key, targets in sorted(edges.items())}
        self.receipt = {"schema": SCHEMA, "scope": "explicit_parameter_annotation_static_relationship",
            "runtime_dispatch_proven": False,
            "source_sha256": {path: _sha(source.encode("utf-8")) for path, source in sorted(self._sources.items())},
            "tainted_modules": sorted(self._tainted_modules),
            "tainted_classes": [{"path": path, "symbol": symbol} for path, symbol in sorted(self._tainted_classes)],
            "definitions": [self._definition_fact(key) for key in sorted(self._windows)],
            "accepted": accepted,
            "declined": sorted(self._declined, key=lambda item: (item["path"], item.get("symbol", ""),
                item.get("parameter", ""), item["reason"]))}

    def _parse(self):
        for path, source in sorted(self._sources.items()):
            self._check()
            if (type(path) is not str or type(source) is not str or not path
                    or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts
                    or str(PurePosixPath(path)) != path):
                raise ValueError("Literal repository-relative paths and source strings required")
            source.encode("utf-8")
            if PurePosixPath(path).suffix != ".py":
                continue
            module = _module(path)
            self._modules[module].append(path)
            try:
                tree = ast.parse(source, filename=path)
            except (SyntaxError, RecursionError, OverflowError, ValueError):
                self._declined.append({"path": path, "reason": "parse_error"})
                continue
            self._trees[path] = tree

            def definitions(node, qualified=(), parent=None, depth=0):
                for child in _scope_nodes(node):
                    self._check()
                    if isinstance(child, (ast.ClassDef, *_FUNCTIONS)):
                        symbol = ".".join([*qualified, child.name])
                        key = (path, symbol, *_span(child))
                        self._definitions[key] = child
                        direct = child in node.body
                        if isinstance(child, ast.ClassDef):
                            self._classes[(path, symbol)] = (child, depth == 0 and direct)
                        else:
                            eligible = direct and (depth == 0 or depth == 1 and isinstance(node, ast.ClassDef))
                            self._functions.append((path, symbol, child, parent, eligible))
                        definitions(child, (*qualified, child.name), child, depth + 1)
            definitions(tree)

    def _validate_windows(self, snippets, keys):
        ids = set()
        for snippet in snippets:
            self._check()
            if not isinstance(snippet, Snippet):
                raise ValueError("Original Snippet dataclasses required")
            if (type(snippet.id) is not str or not snippet.id or snippet.id in ids
                    or type(snippet.path) is not str or type(snippet.symbol) is not str
                    or type(snippet.start_line) is not int or type(snippet.end_line) is not int
                    or not 1 <= snippet.start_line <= snippet.end_line
                    or snippet.path not in self._sources or type(snippet.text) is not str):
                raise ValueError("Invalid or duplicate source window")
            ids.add(snippet.id)
            literal = "".join(physical_lines(self._sources[snippet.path])[snippet.start_line - 1:snippet.end_line])
            if (snippet.text != literal or snippet.content_hash != _sha(literal.encode("utf-8"))
                    or len(physical_lines(literal)) != snippet.end_line - snippet.start_line + 1
                    or snippet.id != f"{snippet.path}:{snippet.symbol}:{snippet.start_line}-{snippet.end_line}"):
                raise ValueError("Source window does not bind literal captured source")
            if PurePosixPath(snippet.path).suffix != ".py":
                continue
            key = keys.get(snippet.id)
            if (not isinstance(key, (tuple, list)) or len(key) != 4
                    or list(key[:2]) != [snippet.path, snippet.symbol]
                    or type(key[2]) is not int or type(key[3]) is not int
                    or not 1 <= key[2] <= snippet.start_line <= snippet.end_line <= key[3]):
                raise ValueError("Exact canonical definition key required")
            key = tuple(key)
            if snippet.path in self._trees and (key not in self._definitions
                    or not isinstance(self._definitions[key], _FUNCTIONS)):
                raise ValueError("Canonical definition does not match full source AST")
            self._windows[key].append(snippet)

    def _module_bindings(self):
        for path, tree in self._trees.items():
            self._check()
            bindings = defaultdict(list)
            module = _module(path)
            for node in _scope_nodes(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    for alias in node.names:
                        name = alias.asname or alias.name.split(".")[0]
                        kind, target = _import_target(node, alias, module, path)
                        bindings[name].append((kind if node in tree.body else "unknown", target, node))
                elif isinstance(node, (ast.ClassDef, *_FUNCTIONS)):
                    kind = "class" if isinstance(node, ast.ClassDef) and node in tree.body else "unknown"
                    bindings[node.name].append((kind, (path, node.name), node))
                elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                    bindings[node.id].append(("unknown", None, node))
                elif isinstance(node, ast.ExceptHandler) and node.name:
                    bindings[node.name].append(("unknown", None, node))
                elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
                    bindings[node.name].append(("unknown", None, node))
                elif isinstance(node, ast.MatchMapping) and node.rest:
                    bindings[node.rest].append(("unknown", None, node))
                elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                    # A comprehension target is local, but its assignment
                    # expressions bind the containing module scope.
                    for child in ast.walk(node):
                        if isinstance(child, ast.NamedExpr) and isinstance(child.target, ast.Name):
                            bindings[child.target.id].append(("unknown", None, child))
            # Cross-scope declarations can mutate a module binding later.
            for node in ast.walk(tree):
                if isinstance(node, ast.Global):
                    for name in node.names:
                        bindings[name].append(("unknown", None, node))
            self._bindings[path] = bindings

    def _resolve_export(self, path, name, seen=frozenset()):
        marker = (path, name)
        if (marker in seen or "*" in self._bindings.get(path, {})
                or not self._mutation_scan and _module(path) in self._tainted_modules):
            return None
        entries = self._bindings.get(path, {}).get(name, ())
        if len(entries) != 1:
            return None
        kind, target, node = entries[0]
        if kind == "class":
            return target, [_witness(path, self._sources[path], node)]
        if kind == "export":
            result = self._qualified(target, seen | {marker})
        elif kind == "module":
            result = ("module", target, [_witness(path, self._sources[path], node)])
        else:
            return None
        if result is None:
            return None
        if result[0] == "module":
            return result[0], result[1], [_witness(path, self._sources[path], node), *result[2]]
        return result[0], [_witness(path, self._sources[path], node), *result[1]]

    def _qualified(self, reference, seen=frozenset()):
        parts = reference.split(".")
        for length in range(len(parts) - 1, 0, -1):
            paths = self._modules.get(".".join(parts[:length]), ())
            if len(paths) != 1 or paths[0] not in self._trees:
                continue
            if len(parts) - length != 1:
                return None
            result = self._resolve_export(paths[0], parts[-1], seen)
            if result is not None or parts[-1] in self._bindings[paths[0]]:
                return result
            break
        # ``from package import module`` can name an inventoried submodule.
        paths = self._modules.get(reference, ())
        if len(paths) == 1 and paths[0] in self._trees:
            return "module", reference, []
        return None

    def _reference(self, path, reference):
        """Resolve a literal class/module namespace, without evaluating source."""
        head, *tail = reference.split(".")
        result = self._resolve_export(path, head)
        if result is None:
            return None
        if result[0] == "module":
            if not tail:
                return "module", result[1], result[2]
            if len(tail) != 1:
                return None
            resolved = self._qualified(".".join([result[1], *tail]))
            if resolved is None:
                return None
            if resolved[0] == "module":
                return "module", resolved[1], [*result[2], *resolved[2]]
            return "class", resolved[0], [*result[2], *resolved[1]]
        return ("class", result[0], result[1]) if not tail else None

    def _resolve(self, path, reference):
        result = self._reference(path, reference)
        return (result[1], result[2]) if result is not None and result[0] == "class" else None

    def _taint_module(self, module):
        if module in self._tainted_modules:
            return
        self._tainted_modules.add(module)
        # Escaping a namespace can mutate reexported classes/modules as well.
        for path in self._modules.get(module, ()):
            for name in self._bindings.get(path, {}):
                self._check()
                result = self._resolve_export(path, name)
                if result is None:
                    continue
                if result[0] == "module":
                    self._taint_module(result[1])
                else:
                    self._tainted_classes.add(result[0])

    def _taint(self, reference):
        if reference is None:
            return
        if reference[0] == "module":
            self._taint_module(reference[1])
        else:
            self._tainted_classes.add(reference[1])

    def _safe_typecheck(self, path, node, parents):
        child, parent = node, parents.get(node)
        while isinstance(parent, ast.Tuple):
            child, parent = parent, parents.get(parent)
        if (not isinstance(parent, ast.Call) or not isinstance(parent.func, ast.Name)
                or parent.func.id not in {"isinstance", "issubclass"}
                or len(parent.args) != 2 or parent.args[1] is not child
                or parent.func.id in self._bindings[path] or "builtins" in self._tainted_modules):
            return False
        scope = parents.get(parent)
        while scope is not None:
            if isinstance(scope, _FUNCTIONS):
                bound = [*_bound_names(scope), *(arg.arg for arg in _parameters(scope))]
                if parent.func.id in bound:
                    return False
            elif isinstance(scope, ast.ClassDef) and parent.func.id in _bound_names(scope):
                return False
            scope = parents.get(scope)
        return True

    def _escapes(self, path, tree, *, modules_only):
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        annotations = set()
        for node in ast.walk(tree):
            annotation = (node.annotation if isinstance(node, (ast.arg, ast.AnnAssign)) else
                          node.returns if isinstance(node, _FUNCTIONS) else None)
            if annotation is not None:
                annotations.update(ast.walk(annotation))
        for node in ast.walk(tree):
            self._check()
            if node in annotations or not isinstance(node, (ast.Name, ast.Attribute)) or not isinstance(node.ctx, ast.Load):
                continue
            parent = parents.get(node)
            if isinstance(parent, ast.Attribute) and parent.value is node:
                continue
            dotted = _dotted(node)
            result = self._reference(path, dotted) if dotted else None
            if result is None:
                continue
            if (result[0] == "module") != modules_only:
                continue
            if isinstance(parent, ast.Call) and parent.func is node:
                continue
            if result[0] == "class" and self._safe_typecheck(path, node, parents):
                continue
            self._taint(result)

    def _namespace_mutations(self, path, tree):
        """Decline direct shared namespaces, not arbitrary executed-string effects.

        Implicit exec/eval within an unrelated function does not establish a
        module/class write. Likewise a literal isolated globals dictionary is
        not the captured namespace. Effects through executed strings, local frame
        dictionaries and external imports remain unproven runtime behavior.
        """
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        classes = {node: key for key, (node, _) in self._classes.items()}

        def scope(node):
            child, parent = node, parents.get(node)
            while parent is not None:
                if isinstance(parent, ast.ClassDef) and child in parent.body:
                    return parent
                if isinstance(parent, _FUNCTIONS):
                    # Defaults, decorators and annotations execute in the
                    # surrounding scope; the function body has its own locals.
                    if child in parent.body:
                        return parent
                if isinstance(parent, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                    return parent
                child, parent = parent, parents.get(parent)
            return tree

        def taint_scope(owner):
            if owner is tree:
                self._taint_module(_module(path))
            elif owner in classes:
                self._tainted_classes.add(classes[owner])

        for node in ast.walk(tree):
            self._check()
            if not isinstance(node, ast.Call):
                continue
            name = (_dotted(node.func) or "").split(".")[-1]
            if name in {"exec", "eval"}:
                namespace = node.args[1] if len(node.args) > 1 else None
                if isinstance(namespace, ast.Dict):
                    continue
                taint_scope(scope(node))
            elif name in {"globals", "locals"}:
                parent = parents.get(node)
                # A discarded dictionary or direct key read does not escape the
                # mapping; writes, aliasing and passed mappings are uncertain.
                if isinstance(parent, ast.Expr) or isinstance(parent, ast.Subscript) and isinstance(parent.ctx, ast.Load):
                    continue
                if name == "globals":
                    self._taint_module(_module(path))
                else:
                    taint_scope(scope(node))

    def _class_mutations(self):
        # Mutations are collected against the original static namespace; every
        # final resolution subsequently sees the complete conservative taint.
        self._mutation_scan = True
        for path, tree in self._trees.items():
            self._check()
            for node in ast.walk(tree):
                self._check()
                if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
                    owner = _dotted(node.value)
                    self._taint(self._reference(path, owner) if owner else None)
                elif isinstance(node, ast.Attribute) and node.attr == "__dict__":
                    owner = _dotted(node.value)
                    self._taint(self._reference(path, owner) if owner else None)
                elif (isinstance(node, ast.Call) and (_dotted(node.func) or "").split(".")[-1] in _DYNAMIC
                        and node.args):
                    value = _dotted(node.args[0])
                    self._taint(self._reference(path, value) if value else None)
            self._namespace_mutations(path, tree)
        for path, tree in self._trees.items():
            self._escapes(path, tree, modules_only=True)
        for path, tree in self._trees.items():
            self._escapes(path, tree, modules_only=False)
        for key, (cls, _) in self._classes.items():
            for function in cls.body:
                if not isinstance(function, _FUNCTIONS):
                    continue
                args = [*function.args.posonlyargs, *function.args.args]
                if not args or any(_dotted(d) == "staticmethod" for d in function.decorator_list):
                    continue
                receiver = args[0].arg
                for node in ast.walk(function):
                    if (isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del))
                            and isinstance(node.value, ast.Name) and node.value.id == receiver):
                        self._tainted_methods.add((*key, node.attr))
                    elif (isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
                            and isinstance(node.value, ast.Name) and node.value.id == receiver):
                        self._tainted_classes.add(key)
                    elif (isinstance(node, ast.Call) and (_dotted(node.func) or "").split(".")[-1] in _DYNAMIC
                            and node.args):
                        if isinstance(node.args[0], ast.Name) and node.args[0].id == receiver:
                            self._tainted_classes.add(key)
                        if (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                                and node.func.value.id == receiver):
                            self._tainted_classes.add(key)
        self._mutation_scan = False

    def _definition_fact(self, key):
        path, symbol, start, end = key
        literal = "".join(physical_lines(self._sources[path])[start - 1:end])
        return {"path": path, "symbol": symbol, "definition_key": list(key), "source": literal,
            "source_sha256": _sha(literal.encode("utf-8")), "definition_sha256": _sha(literal.encode("utf-8")),
            "full_definition": key in self._definitions,
            "window_ids": sorted(snippet.id for snippet in self._windows[key])}

    def _unique_definition(self, path, symbol):
        keys = [key for key in self._windows if key[:2] == (path, symbol)]
        # A second source definition stays ambiguous even if extraction omitted it.
        all_keys = [key for key in self._definitions if key[:2] == (path, symbol)]
        return keys[0] if len(keys) == len(all_keys) == 1 else None

    def _method(self, class_key, method):
        cls, eligible = self._classes.get(class_key, (None, False))
        if (not eligible or cls.bases or cls.keywords or cls.decorator_list
                or getattr(cls, "type_params", ())
                or class_key in self._tainted_classes or (*class_key, method) in self._tainted_methods):
            return None
        bindings = _bound_names(cls)
        if bindings.count(method) != 1 or any(name in bindings for name in _DISPATCH_HOOKS):
            return None
        matches = [node for node in cls.body if isinstance(node, _FUNCTIONS) and node.name == method]
        if len(matches) != 1:
            return None
        node = matches[0]
        if node.decorator_list:
            if (len(node.decorator_list) != 1 or _dotted(node.decorator_list[0]) != "staticmethod"
                    or "staticmethod" in self._bindings[class_key[0]] or "staticmethod" in bindings
                    or "builtins" in self._tainted_modules):
                return None
        path, symbol = class_key
        return self._unique_definition(path, f"{symbol}.{method}")

    def _receiver_reason(self, function, name):
        if name in _bound_names(function):
            return "receiver_rebound_or_scope_declared"
        for node in _scope_nodes(function):
            if isinstance(node, _SCOPES) and any(isinstance(child, ast.Name) and child.id == name
                                                for child in ast.walk(node)):
                return "receiver_nested_scope"
        parents = {child: parent for parent in _scope_nodes(function) for child in ast.iter_child_nodes(parent)}
        for node in _scope_nodes(function):
            self._check()
            if not isinstance(node, ast.Name) or node.id != name:
                continue
            parent = parents.get(node)
            call = parents.get(parent)
            if not (isinstance(parent, ast.Attribute) and parent.value is node and isinstance(parent.ctx, ast.Load)
                    and isinstance(call, ast.Call) and call.func is parent
                    and parent.attr not in _DYNAMIC):
                return "receiver_alias_escape_or_dynamic_attribute"
        return None

    def _calls(self):
        accepted = []
        for path, symbol, function, parent, eligible in sorted(self._functions, key=lambda item: (item[0], item[1], item[2].lineno)):
            self._check()
            caller_key = self._unique_definition(path, symbol)
            for arg in _parameters(function):
                self._check()
                if arg.annotation is None:
                    continue
                base = {"path": path, "symbol": symbol, "parameter": arg.arg,
                        "annotation": _witness(path, self._sources[path], arg.annotation)}

                def decline(reason):
                    self._declined.append({**base, "reason": reason})

                if not eligible or caller_key is None:
                    decline("nested_conditional_or_unindexed_caller")
                    continue
                if len(self._modules[_module(path)]) != 1:
                    decline("module_ambiguous")
                    continue
                if (getattr(function, "type_params", ())
                        or isinstance(parent, ast.ClassDef) and getattr(parent, "type_params", ())):
                    decline("generic_annotation_scope_unsupported")
                    continue
                reference = _annotation(arg.annotation)
                if not reference or arg in (function.args.vararg, function.args.kwarg):
                    decline("unsupported_annotation_or_variadic")
                    continue
                if isinstance(parent, ast.ClassDef) and reference.split(".")[0] in _bound_names(parent):
                    decline("annotation_shadowed_by_class_scope")
                    continue
                reason = self._receiver_reason(function, arg.arg)
                if reason:
                    decline(reason)
                    continue
                resolved = self._resolve(path, reference)
                if resolved is None:
                    decline("annotation_unresolved_or_ambiguous")
                    continue
                class_key, resolution = resolved
                deferred = isinstance(arg.annotation, ast.Constant) or any(
                    isinstance(node, ast.ImportFrom) and node.module == "__future__"
                    and any(alias.name == "annotations" for alias in node.names)
                    for node in self._trees[path].body)
                if not deferred and any(witness["path"] == path and witness["end_line"] >= function.lineno
                                        for witness in resolution):
                    decline("annotation_binding_not_yet_available")
                    continue
                if len(self._modules[_module(class_key[0])]) != 1:
                    decline("module_ambiguous")
                    continue
                calls = [node for node in _scope_nodes(function) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == arg.arg]
                for call in calls:
                    self._check()
                    callee_key = self._method(class_key, call.func.attr)
                    if callee_key is None:
                        decline("method_class_or_dispatch_unresolved")
                        continue
                    accepted.append({"caller": self._definition_fact(caller_key),
                        "callee": self._definition_fact(callee_key), "parameter": arg.arg,
                        "annotation": base["annotation"], "annotation_name": reference,
                        "call": _witness(path, self._sources[path], call),
                        "class": {"path": class_key[0], "symbol": class_key[1]},
                        "resolution": resolution})
        return sorted(accepted, key=lambda fact: (fact["caller"]["path"], fact["caller"]["symbol"],
            fact["parameter"], fact["call"]["start_byte"], fact["callee"]["path"], fact["callee"]["symbol"]))

    def dependencies(self, snippet, limit=1):
        """Graph-compatible first-window lookup; no packing or inference occurs."""
        if type(limit) is not int or limit < 0:
            raise ValueError("limit must be a nonnegative integer")
        output = []
        for path, symbol in self.edges.get((snippet.path, snippet.symbol), ()):
            key = self._unique_definition(path, symbol)
            if key is not None:
                output.append(min(self._windows[key], key=lambda item: (item.start_line, item.id)))
            if len(output) >= limit:
                break
        return output[:limit]

    def witnesses(self):
        """Detached finite JSON facts for a downstream terminal guard/auditor."""
        return deepcopy(self.receipt)
