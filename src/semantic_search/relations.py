"""Conservative static call references used to add bounded source context."""
from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import PurePosixPath


def module_name(path):
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[0] == "src":
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
    """Visit one lexical scope; nested bodies and comprehension bindings differ."""
    for child in ast.iter_child_nodes(node):
        yield child
        if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            continue
        yield from _scope_nodes(child)


def _bindings(node, module, qualified, package, receiver=None):
    writes = defaultdict(list)
    attributes = set()

    def bind(target, entry):
        if isinstance(target, ast.Name):
            writes[target.id].append(entry)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                bind(item, ("unknown", None, entry[2]))
        elif isinstance(target, ast.Attribute):
            attributes.add(_dotted(target))

    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        args += [arg for arg in (node.args.vararg, node.args.kwarg) if arg]
        for arg in args:
            writes[arg.arg].append(("known" if receiver and arg.arg == receiver[0] else "unknown",
                                    receiver[1] if receiver and arg.arg == receiver[0] else None, 0))
    for child in _scope_nodes(node):
        line = getattr(child, "lineno", 0)
        if isinstance(child, ast.Import):
            for alias in child.names:
                writes[alias.asname or alias.name.split(".")[0]].append(
                    ("known", alias.name if alias.asname else alias.name.split(".")[0], line))
        elif isinstance(child, ast.ImportFrom):
            # Going beyond the package root is invalid, not an invented import.
            if child.level and child.level > len(package):
                prefix = None
            else:
                base = package[:len(package) - child.level + 1] if child.level else []
                prefix = ".".join([*base, *(child.module or "").split(".")]).strip(".")
            for alias in child.names:
                if alias.name != "*":
                    value = f"{prefix}.{alias.name}".strip(".") if prefix is not None else None
                    writes[alias.asname or alias.name].append(("known" if value else "unknown", value, line))
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            writes[child.name].append(("known", ".".join([module, *qualified, child.name]), line))
        elif isinstance(child, ast.Assign):
            for target in child.targets:
                bind(target, ("constructed", child.value, line))
        elif isinstance(child, ast.AnnAssign):
            bind(child.target, ("constructed", child.value, line))
        elif isinstance(child, (ast.AugAssign, ast.NamedExpr)):
            bind(child.target, ("unknown", None, line))
        elif isinstance(child, (ast.For, ast.AsyncFor)):
            bind(child.target, ("unknown", None, line))
        elif isinstance(child, (ast.With, ast.AsyncWith)):
            for item in child.items:
                if item.optional_vars:
                    bind(item.optional_vars, ("unknown", None, line))
        elif isinstance(child, ast.ExceptHandler) and child.name:
            writes[child.name].append(("unknown", None, line))
        elif isinstance(child, ast.Delete):
            for target in child.targets:
                bind(target, ("unknown", None, line))
        elif isinstance(child, (ast.Global, ast.Nonlocal)):
            # Assignment to a different lexical scope is deliberately unresolved.
            for name in child.names:
                writes[name].append(("unknown", None, line))
    return writes, attributes


class ReferenceGraph:
    """Resolve explicit calls, imports, and known-class methods without execution.

    Parameters, ambiguous/reassigned bindings, factories, reflection, inherited
    dispatch, and runtime monkey patches remain unresolved. A reference supplies
    context; it is never evidence that the returned snippet implements a query.
    """

    def __init__(self, sources, snippets):
        self.windows = defaultdict(list)
        for snippet in snippets:
            self.windows[(snippet.path, snippet.symbol)].append(snippet)
        names = {f"{module_name(path)}.{symbol}": (path, symbol) for path, symbol in self.windows}
        self.edges = defaultdict(list)
        classes, functions, module_scopes = set(), [], {}
        trees = {path: ast.parse(source, filename=path) for path, source in sources.items()}
        for path, tree in trees.items():
            module = module_name(path)
            package = module.split(".") if PurePosixPath(path).name == "__init__.py" else module.split(".")[:-1]
            module_scopes[module] = _bindings(tree, module, (), package)
            # A function body can call a module function defined later: the
            # module is initialized before that body is invoked.
            module_scopes[module] = ({name: [(kind, value, 0) for kind, value, _ in entries]
                                      for name, entries in module_scopes[module][0].items()},
                                     module_scopes[module][1])

            def definitions(node, qualified=(), enclosing=(), owner=None):
                for child in _scope_nodes(node):
                    if isinstance(child, ast.ClassDef):
                        name = ".".join([module, *qualified, child.name])
                        classes.add(name)
                        definitions(child, (*qualified, child.name), enclosing, name)
                    elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        decorators = {_dotted(decorator) for decorator in child.decorator_list}
                        args = [*child.args.posonlyargs, *child.args.args]
                        receiver = None
                        # Only a direct method receives an implicit class/instance.
                        if (owner and isinstance(node, ast.ClassDef) and args
                                and not decorators.intersection({"staticmethod", "builtins.staticmethod"})):
                            receiver = (args[0].arg, owner)
                        scope = _bindings(child, module, (*qualified, child.name), package, receiver)
                        functions.append((path, module, child, (*qualified, child.name), (scope, *enclosing)))
                        definitions(child, (*qualified, child.name), (scope, *enclosing), owner)
            definitions(tree)

        def resolve(reference, scopes, line, resolving=frozenset()):
            if not reference:
                return None
            head, *tail = reference.split(".")
            for bindings, attributes in scopes:
                if reference in attributes:
                    return None
                if head not in bindings:
                    continue
                entries = bindings[head]
                if len(entries) != 1:
                    return None
                kind, value, defined_at = entries[0]
                marker = (id(bindings), head)
                if kind == "unknown" or defined_at > line or marker in resolving:
                    return None
                if kind == "constructed":
                    if not isinstance(value, ast.Call):
                        return None
                    value = resolve(_dotted(value.func), scopes, defined_at, resolving | {marker})
                    if value not in classes:
                        return None
                return ".".join([value, *tail])
            return None

        for path, module, function, qualified, enclosing in functions:
            key = (path, ".".join(qualified))
            scopes = (*enclosing, module_scopes[module])
            for call in (node for node in _scope_nodes(function) if isinstance(node, ast.Call)):
                resolved = resolve(_dotted(call.func), scopes, call.lineno)
                target = names.get(resolved)
                if target is not None and target != key and target not in self.edges[key]:
                    self.edges[key].append(target)

    def dependencies(self, snippet, limit=1):
        if type(limit) is not int or limit < 0:
            raise ValueError("limit must be a nonnegative integer")
        output = []
        for target in self.edges.get((snippet.path, snippet.symbol), ()):
            if len(output) >= limit:
                break
            windows = sorted(self.windows[target], key=lambda s: (s.start_line, s.id))
            if windows:
                output.append(windows[0])
        return output
