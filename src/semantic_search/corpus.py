"""Extract location-preserving Python implementation snippets from a repository."""

from __future__ import annotations

import ast
import hashlib
import io
import subprocess
import tokenize
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from semantic_search.source import physical_lines


@dataclass(frozen=True)
class Snippet:
    id: str
    path: str
    symbol: str
    start_line: int
    end_line: int
    text: str
    content_hash: str


def _source_files(repo: Path, source_root: Path) -> list[Path]:
    """Prefer tracked files, falling back to the supplied directory outside Git."""
    relative_root = source_root.relative_to(repo)
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "-z", "--", str(relative_root)],
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result is not None and result.returncode == 0:
        candidates = [repo / name.decode("utf-8", "surrogateescape")
                      for name in result.stdout.split(b"\0") if name]
    else:
        candidates = list(source_root.rglob("*.py"))
    return sorted(
        path for path in candidates
        if path.suffix == ".py" and path.is_file()
        and path.resolve().is_relative_to(source_root)
    )


class _Definitions(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: list[str] = []
        self.functions: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
        self.docstrings: list[tuple[int, int]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.append(node.name)
        self._record_docstring(node)
        self.generic_visit(node)
        self.names.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.names.append(node.name)
        if not _is_unimplemented(node):
            self.functions.append((".".join(self.names), node))
        self._record_docstring(node)
        self.generic_visit(node)
        self.names.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def _record_docstring(
        self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:
        if not node.body:
            return
        first = node.body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            self.docstrings.append((first.lineno, first.end_lineno or first.lineno))


def _is_docstring(statement: ast.stmt) -> bool:
    return (isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str))


def _is_placeholder(statement: ast.stmt) -> bool:
    if isinstance(statement, ast.Pass):
        return True
    if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
        return statement.value.value is Ellipsis
    if isinstance(statement, ast.Raise):
        exception = statement.exc
        if isinstance(exception, ast.Call):
            exception = exception.func
        return ((isinstance(exception, ast.Name) and exception.id == "NotImplementedError")
                or (isinstance(exception, ast.Attribute) and exception.attr == "NotImplementedError"))
    return False


def _is_unimplemented(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in node.decorator_list:
        if ((isinstance(decorator, ast.Name) and decorator.id == "overload")
                or (isinstance(decorator, ast.Attribute) and decorator.attr == "overload")):
            return True
    body = node.body[1:] if node.body and _is_docstring(node.body[0]) else node.body
    return all(_is_placeholder(statement) for statement in body)


def _implementation_lines(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    docstrings: list[tuple[int, int]],
) -> set[int]:
    """Locate body statements, excluding the outer signature and every docstring."""
    lines: set[int] = set()
    for statement in ast.walk(node):
        if (statement is node or not isinstance(statement, ast.stmt)
                or _is_docstring(statement)):
            continue
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # Creating a nested definition is behavior; its children supply body lines.
            final_line = max(statement.lineno, statement.body[0].lineno - 1)
        else:
            final_line = statement.end_lineno or statement.lineno
        statement_lines = set(range(statement.lineno, final_line + 1))
        if hasattr(statement, "body") or isinstance(statement, ast.Match):
            for start, end in docstrings:
                statement_lines.difference_update(range(start, end + 1))
        lines.update(statement_lines)
    return lines


def extract_snippets(
    repo: Path,
    source_root: str = "src/flask",
    max_lines: int = 100,
    overlap_lines: int = 20,
    *,
    sources: Mapping[str, str] | None = None,
) -> list[Snippet]:
    """Return functions and methods, windowing long bodies without changing source.

    Line numbers are inclusive and one-based. Overload and placeholder definitions
    are omitted, along with windows containing only signatures and docstrings.
    Overlapping windows still cover every implementation statement's source lines.
    Classes are traversed for methods, rather than indexed again as whole files.
    Optional sources maps repository-relative paths to already captured decoded
    source; this mode parses exactly that membership without rereading files.
    """
    if max_lines < 1 or not 0 <= overlap_lines < max_lines:
        raise ValueError("Require max_lines > 0 and 0 <= overlap_lines < max_lines")
    repo = Path(repo).resolve()
    root = (repo / source_root).resolve()
    if not root.is_relative_to(repo):
        raise ValueError("source_root must be inside the repository")
    if sources is None and not root.is_dir():
        raise FileNotFoundError(f"Source directory does not exist: {root}")
    if sources is None:
        entries = ((path, None) for path in _source_files(repo, root))
    else:
        if not isinstance(sources, Mapping):
            raise ValueError("sources must map repository-relative paths to source strings")
        captured = []
        for relative_path, source in sources.items():
            if not isinstance(relative_path, str) or not isinstance(source, str):
                raise ValueError("Captured source paths and contents must be strings")
            relative = Path(relative_path)
            path = repo / relative
            if (relative.is_absolute() or ".." in relative.parts or path.suffix != ".py"
                    or not path.is_relative_to(root) or not path.resolve().is_relative_to(root)):
                raise ValueError("Captured source paths must be Python files inside source_root")
            captured.append((path, source))
        entries = sorted(captured)
    snippets: list[Snippet] = []
    for path, source in entries:
        if source is None:
            raw = path.read_bytes()
            encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
            source = raw.decode(encoding)
        lines = physical_lines(source)
        definitions = _Definitions()
        definitions.visit(ast.parse(source, filename=str(path)))
        relative_path = path.relative_to(repo).as_posix()
        for symbol, node in definitions.functions:
            implementation_lines = _implementation_lines(node, definitions.docstrings)
            first_line = min([node.lineno, *(item.lineno for item in node.decorator_list)])
            final_line = node.end_lineno or node.lineno
            start = first_line
            while start <= final_line:
                end = min(start + max_lines - 1, final_line)
                if any(start <= line <= end for line in implementation_lines):
                    text = "".join(lines[start - 1:end])
                    snippets.append(Snippet(
                        id=f"{relative_path}:{symbol}:{start}-{end}",
                        path=relative_path,
                        symbol=symbol,
                        start_line=start,
                        end_line=end,
                        text=text,
                        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    ))
                if end == final_line:
                    break
                start += max_lines - overlap_lines
    return snippets
