#!/usr/bin/env python3
"""Experimental C# source extractor; disconnected from production/index/editor."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from bisect import bisect_right
from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePosixPath

PINS = {"tree-sitter": "0.26.0", "tree-sitter-c-sharp": "0.23.5"}
TYPE_NODES = {"class_declaration", "struct_declaration", "interface_declaration", "record_declaration"}
MEMBER_NODES = {"method_declaration", "constructor_declaration"}


@dataclass(frozen=True)
class Definition:
    symbol: str
    kind: str
    start_byte: int
    end_byte: int
    body_start_byte: int
    body_end_byte: int


@dataclass(frozen=True)
class Window:
    id: str
    path: str
    symbol: str
    start_line: int
    end_line: int
    text: str
    content_hash: str


def physical_lines(source):
    return [line for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", source) if line]


def validate_path(path):
    if (not isinstance(path, str) or not path
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or any(unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for char in path)):
        raise ValueError("Prototype path cannot contain empty/dot components or control characters")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or ".." in parsed.parts or parsed.suffix != ".cs" or "\\" in str(path):
        raise ValueError("Prototype path must be a repository-relative .cs path")
    return parsed.as_posix()


def member_symbol(namespace, types, kind, name, signature_tokens):
    """Literal token identity, not compiler overload or symbol resolution."""
    identity = json.dumps([kind, signature_tokens], ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return ".".join((*namespace, *types, name)) + "#" + digest


def reject_ambiguous_symbols(definitions):
    seen = set()
    for definition in definitions:
        if definition.symbol in seen:
            raise ValueError("Ambiguous duplicate C# symbol, including possible preprocessor branches")
        seen.add(definition.symbol)


def windows_for(source, path, definitions, max_lines=160, overlap_lines=40):
    path = validate_path(path)
    if (type(max_lines) is not int or type(overlap_lines) is not int or max_lines < 1
            or not 0 <= overlap_lines < max_lines):
        raise ValueError("Window size/overlap must be valid integers")
    reject_ambiguous_symbols(definitions)
    raw = source.encode("utf-8")
    lines = physical_lines(source)
    offsets, offset = [], 0
    for line in lines:
        offsets.append(offset)
        offset += len(line.encode("utf-8"))
    result = []
    for definition in definitions:
        if not (0 <= definition.start_byte <= definition.body_start_byte
                < definition.body_end_byte <= definition.end_byte <= len(raw)):
            raise ValueError("Definition/body bytes must be inside exact source")
        start = bisect_right(offsets, definition.start_byte)
        final = bisect_right(offsets, definition.end_byte - 1)
        while start <= final:
            end = min(start + max_lines - 1, final)
            text = "".join(lines[start - 1:end])
            identity = f"{path}:{definition.symbol}:{start}-{end}"
            result.append(Window(identity, path, definition.symbol, start, end, text,
                                 hashlib.sha256(text.encode("utf-8")).hexdigest()))
            if end == final:
                break
            start = end - overlap_lines + 1
    return result


def load_parser():
    for package, expected in PINS.items():
        try:
            actual = version(package)
        except PackageNotFoundError as error:
            raise ValueError("Prototype requires the isolated pinned C# grammar environment") from error
        if actual != expected:
            raise ValueError(f"Prototype parser {package} requires {expected}, found {actual}")
    from tree_sitter import Language, Parser
    import tree_sitter_c_sharp
    language = Language(tree_sitter_c_sharp.language())
    if language.abi_version != 15:
        raise ValueError("Prototype grammar ABI must be the qualified 15")
    return Parser(language)


def _tokens(node, raw):
    if node is None or node.type == "comment":
        return []
    if not node.children:
        return [(node.type, raw[node.start_byte:node.end_byte].decode("utf-8"))]
    return [token for child in node.children for token in _tokens(child, raw)]


def _name(node, raw):
    if node is None:
        raise ValueError("Missing named C# declaration")
    return "".join(text for _, text in _tokens(node, raw))


def _nonempty_block(body):
    for child in body.named_children:
        if child.type in {"comment", "empty_statement"}:
            continue
        if child.type != "block" or _nonempty_block(child):
            return True
    return False


def parse_definitions(source, parser=None):
    raw = source.encode("utf-8")
    tree = (parser or load_parser()).parse(raw)
    if tree.root_node.has_error:
        raise SyntaxError("Cannot extract C# source with grammar errors")
    pending = [tree.root_node]
    while pending:
        node = pending.pop()
        if node.is_error or node.is_missing:
            raise SyntaxError("Cannot extract C# source with ERROR/missing nodes")
        pending.extend(node.children)
    result = []

    def visit(node, namespace=(), types=()):
        if node.type == "file_scoped_namespace_declaration":
            raise ValueError("Conditional/nested file-scoped namespaces are unsupported")
        if node.type == "namespace_declaration":
            nested = (*namespace, _name(node.child_by_field_name("name"), raw))
            body = node.child_by_field_name("body")
            if body is None:
                raise ValueError("Namespace body is missing")
            visit(body, nested, types)
            return
        if node.type in TYPE_NODES:
            name = _name(node.child_by_field_name("name"), raw)
            parameters = next((child for child in node.named_children if child.type == "type_parameter_list"), None)
            if parameters is not None:
                arity = sum(child.type == "type_parameter" for child in parameters.named_children)
                name += "`" + str(arity)
            body = node.child_by_field_name("body")
            if body is not None:
                visit(body, namespace, (*types, name))
            return
        if node.type in MEMBER_NODES:
            body = node.child_by_field_name("body")
            if body is None or not types:
                return
            initializer = next((child for child in node.named_children
                                if child.type == "constructor_initializer"), None)
            if body.type == "block" and not _nonempty_block(body) and initializer is None:
                return
            if body.type not in {"block", "arrow_expression_clause"}:
                raise ValueError("Unsupported C# method/constructor body shape")
            name = _name(node.child_by_field_name("name"), raw)
            signature = []
            for child in node.named_children:
                if (child.type in {"parameter_list", "type_parameter_list", "explicit_interface_specifier"}
                        or (child.type == "modifier" and _name(child, raw) == "static")):
                    signature.extend(_tokens(child, raw))
            symbol = member_symbol(namespace, types, node.type, name, signature)
            result.append(Definition(symbol, node.type, node.start_byte, node.end_byte,
                                     body.start_byte, body.end_byte))
            # Local functions/lambdas/accessors are not separately extracted.
            return
        for child in node.named_children:
            visit(child, namespace, types)

    namespace = ()
    file_scoped_seen = False
    for child in tree.root_node.named_children:
        if child.type == "file_scoped_namespace_declaration":
            if file_scoped_seen:
                raise ValueError("Multiple file-scoped namespaces are unsupported")
            namespace = (_name(child.child_by_field_name("name"), raw),)
            file_scoped_seen = True
        else:
            visit(child, namespace)
    reject_ambiguous_symbols(result)
    return result


def extract(source, path, parser=None, max_lines=160, overlap_lines=40):
    validate_path(path)
    definitions = parse_definitions(source, parser)
    return definitions, windows_for(source, path, definitions, max_lines, overlap_lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--path", required=True, help="Literal repository-relative .cs display path")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    raw = args.source.read_bytes()
    definitions, windows = extract(raw.decode("utf-8"), args.path)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump({"status": "experimental prototype; not production language support",
            "source_sha256": hashlib.sha256(raw).hexdigest(), "pins": PINS,
            "definitions": [asdict(item) for item in definitions],
            "windows": [asdict(item) for item in windows],
            "limitations": "No compiler, build-symbol, dependency, source-membership, editor or retrieval qualification."},
            output, ensure_ascii=False, indent=2)
        output.write("\n")


if __name__ == "__main__":
    main()
