"""Portable CPU fault checks; never imports or executes a C# grammar."""
import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location("csharp_preflight", Path(__file__).resolve().parents[1] / "scripts/qualify_csharp_parser.py")
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


def publisher():
    package, pin = "tree-sitter", helper.PINS["tree-sitter"]
    row = {"filename": pin["filename"], "digests": {"sha256": pin["sha256"]},
           "yanked": False, "packagetype": "bdist_wheel", "size": 140820,
           "url": "https://files.pythonhosted.org/packages/verified/" + pin["filename"]}
    return package, pin, {"info": {"name": package, "version": pin["version"]}, "urls": [row]}


def test_exact_publisher_pin_selected():
    package, pin, metadata = publisher()
    assert helper.select_wheel(metadata, package, pin) == metadata["urls"][0]


@pytest.mark.parametrize("fault", ["hash", "yanked", "origin", "duplicate", "version", "bool_size"])
def test_bad_publisher_wheel_fails_before_install(fault):
    package, pin, metadata = publisher()
    row = metadata["urls"][0]
    if fault == "hash":
        row["digests"]["sha256"] = "0" * 64
    elif fault == "yanked":
        row["yanked"] = True
    elif fault == "origin":
        row["url"] = row["url"].replace("files.pythonhosted.org", "untrusted.example")
    elif fault == "duplicate":
        metadata["urls"].append(copy.deepcopy(row))
    elif fault == "version":
        metadata["info"]["version"] = "0.26.1"
    else:
        row["size"] = True
    with pytest.raises(ValueError):
        helper.select_wheel(metadata, package, pin)


def test_exclusive_receipts_preserve_first_attempt(tmp_path):
    path = tmp_path / "receipt.json"
    helper.write_json(path, {"first": True})
    with pytest.raises(FileExistsError):
        helper.write_json(path, {"second": True})
    assert helper.read_json(path) == {"first": True}


def test_physical_bytes_ignore_unicode_line_separator_and_preserve_crlf():
    raw = '\ufefffirst\r\npublic string Text() => "café😎\u2028line";\r\nlast\r'.encode("utf-8")
    start, end = raw.index(b"public"), raw.index(b";", raw.index(b"public")) + 1
    assert helper.physical_span(raw, start, end) == [2, 2]
    for span in ((-1, end), (start, len(raw) + 1), (start, start)):
        with pytest.raises(ValueError):
            helper.physical_span(raw, *span)


def test_frozen_fixture_identity_changes_fail_before_parse():
    case = helper.fixtures()[0]
    case["source"] += " "
    with pytest.raises(ValueError, match="fixture bytes"):
        helper.probe_case(SimpleNamespace(parse=lambda raw: pytest.fail("Parser should not run")), case)


def test_missing_nodes_are_invalid_even_without_root_error():
    case = {"name": "synthetic_missing", "source": "x", "raw_sha256": helper.sha(b"x"),
            "expected_members": [], "expect_invalid": True}
    child = SimpleNamespace(type=";", children=[], start_byte=1, end_byte=1, is_error=False, is_missing=True)
    root = SimpleNamespace(type="compilation_unit", children=[child], start_byte=0, end_byte=1,
                           is_error=False, is_missing=False, has_error=False)
    result = helper.probe_case(SimpleNamespace(parse=lambda raw: SimpleNamespace(root_node=root)), case)
    assert result["checks_passed"] and result["missing"] == [{"type": ";", "start_byte": 1, "end_byte": 1}]


def test_every_positive_fixture_has_literal_member_targets_and_unique_name():
    cases = helper.fixtures()
    assert len(cases) == len({row["name"] for row in cases}) == 9
    assert sum(row["expect_invalid"] for row in cases) == 2
    for row in cases:
        assert helper.sha(row["source"].encode("utf-8")) == row["raw_sha256"]
        if not row["expect_invalid"]:
            assert row["expected_members"]
            assert all(member in row["source"] for member in row["expected_members"])
