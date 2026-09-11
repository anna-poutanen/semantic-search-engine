"""Fake JSON-RPC/usage streams; no native server, auth or hosted model runs."""
from __future__ import annotations

import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile

import pytest


SPEC = importlib.util.spec_from_file_location("usage_probe", Path(__file__).parents[1] / "scripts/probe_usage_notifications.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def breakdown(input_tokens=100, output_tokens=20, cached=80, reasoning=10, *, writes=None):
    result = {"inputTokens": input_tokens, "outputTokens": output_tokens, "cachedInputTokens": cached,
              "reasoningOutputTokens": reasoning, "totalTokens": input_tokens + output_tokens}
    if writes is not None:
        result["cacheWriteInputTokens"] = writes
    return result


def event(method, params, index):
    return {"direction": "received", "stamp": {"utc": f"fake-{index}", "monotonic_ns": index},
            "message": {"method": method, "params": params}}


def turn(method, turn_id, index, *, status="completed"):
    return event(method, {"threadId": "thread", "turn": {"id": turn_id, "status": status, "items": []}}, index)


def usage(turn_id, index, *, last=None, total=None, thread_id="thread"):
    return event("thread/tokenUsage/updated", {"threadId": thread_id, "turnId": turn_id,
                 "tokenUsage": {"last": last or breakdown(), "total": total or breakdown()}}, index)


def planned(turn_id="t1", word="ALPHA"):
    return {"ordinal": 1, "thread_id": "thread", "turn_id": turn_id, "expected_word": word, "attempted": True}


def test_cumulative_snapshots_duplicates_subcategories_and_optional_missing_are_preserved():
    events = [turn("turn/started", "t1", 0), usage("t1", 1), usage("t1", 2),
              usage("t1", 3, last=breakdown(25, 5, 10, 2, writes=3), total=breakdown(125, 25, 90, 12, writes=3)),
              turn("turn/completed", "t1", 4)]
    result = probe.analyze_events(events, [planned()])
    snapshots = result["usage_snapshots"]
    assert len(snapshots) == 3 and snapshots[1]["duplicate_previous_snapshot"] is True
    assert snapshots[0]["last_reported"]["cacheWriteInputTokens"] is None
    assert snapshots[0]["last_reported"]["totalTokens"] == 120  # Never +80 cached +10 reasoning.
    assert snapshots[0]["last_reported"]["uncached_input"] == 20
    assert snapshots[2]["arithmetic_delta_from_previous_thread_snapshot"]["totalTokens"] == 30
    assert result["turns"][0]["reported_thread_total"]["totalTokens"] == 150  # Never120+120+150.
    assert all(snapshot["observed_before_turn_completed"] for snapshot in snapshots)


def test_missing_usage_stays_null_even_with_successful_final_reply():
    events = [turn("turn/started", "t1", 0), event("item/completed", {
        "threadId": "thread", "turnId": "t1", "item": {"type": "agentMessage", "id": "message", "text": "ALPHA"}}, 1),
        turn("turn/completed", "t1", 2)]
    result = probe.analyze_events(events, [planned(), {**planned("t2", "BETA"), "attempted": False}])
    assert result["turns"][0]["word_reply_matches"] is True
    assert result["turns"][0]["reported_last"] is None
    assert result["turns"][0]["reported_thread_total"] is None
    assert result["turns"][1]["completed_status"] is None
    assert result["turns"][1]["notification_count"] == 0


def test_receipts_are_matched_by_exact_thread_and_turn_with_late_updates():
    events = [turn("turn/started", "t1", 0), usage("t1", 1), turn("turn/completed", "t1", 2),
              turn("turn/started", "t2", 3), usage("t1", 4), usage("t2", 5, total=breakdown(200, 40, 160, 20)),
              usage("t2", 6, thread_id="other-thread"), turn("turn/completed", "t2", 7)]
    result = probe.analyze_events(events, [planned(), planned("t2", "BETA")])
    assert [row["notification_count"] for row in result["turns"]] == [2, 1]
    assert result["usage_snapshots"][1]["observed_before_turn_completed"] is False
    assert result["usage_snapshots"][-1]["observed_before_turn_completed"] is None


@pytest.mark.parametrize("field,value", [("inputTokens", None), ("inputTokens", True), ("outputTokens", -1)])
def test_invalid_usage_fields_are_retained_as_issues_without_estimates(field, value):
    malformed = usage("t1", 0)
    malformed["message"]["params"]["tokenUsage"]["last"][field] = value
    result = probe.analyze_events([malformed], [planned()])
    assert result["usage_snapshots"] == [] and len(result["issues"]) == 1
    assert result["turns"][0]["reported_last"] is None


def test_decreasing_cumulative_counter_has_no_inferred_delta():
    result = probe.analyze_events([usage("t1", 0, total=breakdown(200, 40, 100, 20)), usage("t1", 1)])
    assert result["usage_snapshots"][1]["arithmetic_delta_from_previous_thread_snapshot"] is None
    assert "decreased" in result["issues"][0]["classification"]


def test_total_mismatch_is_an_observation_not_silently_replaced():
    reported = breakdown()
    reported["totalTokens"] = 121
    decoded = probe.usage_breakdown(reported)
    assert decoded["totalTokens"] == 121 and decoded["reported_total_matches_input_plus_output"] is False


def test_effective_config_gate_requires_observed_empty_tables_and_all_flags():
    assert probe.config_gate({"config": probe.OVERRIDES})["passed"] is True
    inherited = copy.deepcopy(probe.OVERRIDES)
    inherited["mcp_servers"] = {"external": {"enabled": True}}
    assert probe.config_gate({"config": inherited})["passed"] is False
    missing = dict(probe.OVERRIDES)
    del missing["features.multi_agent_v2"]
    assert probe.config_gate({"config": missing})["passed"] is False
    assert probe.config_gate({"config": {}})["passed"] is False


def test_unmatched_config_exposes_shape_without_entry_names_values_or_digests():
    inherited = copy.deepcopy(probe.OVERRIDES)
    inherited["mcp_servers"] = {"private-server-name": {"headers": {"custom-header": "non-sk-secret-token"},
                                                      "env": {"PRIVATE_TOKEN_NAME": "private-env-secret"}}}
    inherited["developer_instructions"] = "private proprietary instructions"
    inherited["plugins"] = None
    del inherited["hooks"]
    gate = probe.config_gate({"config": inherited})
    observed = gate["observed_requested_settings"]
    assert observed["mcp_servers"]["unmatched_value_shape"]["count"] == 1
    assert observed["plugins"]["unmatched_value_shape"]["is_null"] is True
    assert observed["hooks"]["unmatched_value_shape"]["is_missing"] is True
    assert observed["model"]["known_requested_value"] == probe.MODEL
    encoded = json.dumps(gate)
    for secret in ("private-server-name", "custom-header", "non-sk-secret-token", "PRIVATE_TOKEN_NAME",
                   "private-env-secret", "private proprietary instructions", "sha256"):
        assert secret not in encoded


def test_home_auth_retained_but_api_and_desktop_bridge_environment_removed():
    environment = {"HOME": "/home", "CODEX_HOME": "/home/.codex", "PATH": "/bin",
                   **{name: "do-not-publish" for name in probe.REMOVED_ENV}}
    assert probe.clean_env(environment) == {"HOME": "/home", "CODEX_HOME": "/home/.codex", "PATH": "/bin"}
    argv = probe.command("codex")
    assert "--ignore-user-config" not in argv and "--ignore-rules" not in argv
    assert "model=\"gpt-6.1-sol\"" in argv and "model_reasoning_effort=\"ultra\"" in argv


class FakeProcess:
    def __init__(self, messages):
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO("".join(json.dumps(message) + "\n" for message in messages).encode())
        self.stderr = io.BytesIO()

    def terminate(self):
        pass

    def wait(self, timeout):
        return 0


def test_fake_rpc_startup_gate_blocks_both_prompts_and_preserves_missing_usage(tmp_path, monkeypatch):
    process = FakeProcess([{"id": 1, "result": {}}, {"id": 2, "result": {"config": {}}}])
    monkeypatch.setattr(probe.subprocess, "Popen", lambda *args, **kwargs: process)
    result = probe.execute(tmp_path / "published", tmp_path / "raw", {"command": ["fake"], "turn_timeout_s": 120})
    assert result["diagnostic_model_turns_attempted"] == 0
    assert result["execution_status"] == "failed_or_gate_blocked"
    assert all(turn["reported_last"] is None for turn in result["observations"]["turns"])
    requests = [json.loads(line) for line in process.stdin.getvalue().decode().split("\n") if line]
    assert [request["method"] for request in requests] == ["initialize", "initialized", "config/read"]
    private_fixture = json.loads((tmp_path / "raw/fixture-private.json").read_text())["owned_fixture_path"]
    fixture = Path(private_fixture)
    assert fixture.parent == Path("/private/tmp").resolve() and not fixture.exists()
    assert result["fixture_gate"]["passed"] is True and result["owned_fixture_cleaned"] is True
    assert not (tmp_path / "raw/empty-fixture").exists()
    assert private_fixture not in (tmp_path / "published/events.jsonl").read_text()


def test_fake_two_turn_stream_handles_usage_before_rpc_response_without_summing(tmp_path, monkeypatch):
    processes = []

    def popen(*args, **kwargs):
        fixture = kwargs["cwd"]
        started = {"model": probe.MODEL, "reasoningEffort": probe.EFFORT, "serviceTier": probe.TIER,
                   "modelProvider": "openai", "instructionSources": [], "thread": {"id": "thread", "ephemeral": True},
                   "cwd": str(fixture), "sandbox": {"type": "readOnly"}}
        messages = [{"id": 1, "result": {}}, {"id": 2, "result": {"config": probe.OVERRIDES}},
                    {"id": 3, "result": started}, {"id": 4, "result": {"data": [], "nextCursor": None}}]
        for number, word in enumerate(("ALPHA", "BETA"), 1):
            turn_id = f"t{number}"
            messages += [turn("turn/started", turn_id, 0)["message"],
                         usage(turn_id, 1, total=breakdown(100 * number, 20 * number, 80 * number, 10 * number))["message"],
                         {"id": number + 4, "result": {"turn": {"id": turn_id}}},
                         event("item/completed", {"threadId": "thread", "turnId": turn_id,
                               "item": {"id": f"m{number}", "type": "agentMessage", "text": word}}, 2)["message"],
                         turn("turn/completed", turn_id, 3)["message"]]
        process = FakeProcess(messages)
        processes.append(process)
        return process

    monkeypatch.setattr(probe.subprocess, "Popen", popen)
    result = probe.execute(tmp_path / "published", tmp_path / "raw", {"command": ["fake"], "turn_timeout_s": 120})
    assert result["execution_status"] == "completed" and result["diagnostic_model_turns_attempted"] == 2
    assert all(row["word_reply_matches"] is True for row in result["observations"]["turns"])
    assert result["observations"]["turns"][1]["reported_thread_total"]["totalTokens"] == 240
    publication = (tmp_path / "published/events.jsonl").read_text()
    assert "raw_utf8" not in publication and "raw_line_sha256" in publication
    assert "<effective config withheld" in publication
    assert str(tmp_path) not in publication


def test_arbitrary_inherited_credentials_and_rpc_stderr_never_enter_public_files(tmp_path, monkeypatch):
    secret = "NotAnOpenAIKey_private_customer_credential_123"
    private_instruction = "Proprietary inherited instruction for private customer"
    inherited = copy.deepcopy(probe.OVERRIDES)
    inherited["mcp_servers"] = {"PRIVATE_SERVER_NAME": {"headers": {"CUSTOM_HEADER_NAME": secret},
                                                        "env": {"PRIVATE_TOKEN_NAME": secret}}}
    inherited["plugins"] = {"PRIVATE_PLUGIN_NAME": {"enabled": True, "credential": secret}}
    inherited["hooks"] = {"SessionStart": [{"private_command": secret}]}
    inherited["developer_instructions"] = private_instruction
    messages = [
        {"id": 1, "result": {"userAgent": secret, "privateCustomer": private_instruction}},
        {"method": "PRIVATE_UNKNOWN_NOTIFICATION", "params": {"privateCustomer": secret}},
        {"method": "item/reasoning/textDelta", "params": {"threadId": "thread", "turnId": "t1", "delta": secret}},
        event("item/completed", {"threadId": "thread", "turnId": "t1",
              "item": {"id": "m1", "type": "agentMessage", "text": secret}}, 0)["message"],
        {"id": 2, "result": {"config": inherited}},
    ]
    process = FakeProcess(messages)
    process.stderr = io.BytesIO(f"custom-header={secret}; {private_instruction}\n".encode())
    monkeypatch.setattr(probe.subprocess, "Popen", lambda *args, **kwargs: process)
    result = probe.execute(tmp_path / "published", tmp_path / "raw", {"command": ["fake"], "turn_timeout_s": 120})
    publication = "".join(path.read_text() for path in (tmp_path / "published").iterdir()) + json.dumps(result)
    for private in (secret, private_instruction, "PRIVATE_SERVER_NAME", "CUSTOM_HEADER_NAME", "PRIVATE_TOKEN_NAME",
                    "PRIVATE_PLUGIN_NAME", "PRIVATE_UNKNOWN_NOTIFICATION", "private_command", "custom-header"):
        assert private not in publication
    assert secret in (tmp_path / "raw/raw-events.jsonl").read_text()
    assert result["diagnostic_model_turns_attempted"] == 0


def test_unexpected_agent_final_text_and_usage_extras_are_not_dumped_in_observations():
    secret = "Unexpected_private_model_output_credential"
    notification = usage("t1", 1)
    notification["message"]["params"]["tokenUsage"]["privateHeader"] = secret
    events = [turn("turn/started", "t1", 0), notification,
              event("item/completed", {"threadId": "thread", "turnId": "t1",
                    "item": {"id": "m1", "type": "agentMessage", "text": secret}}, 2),
              turn("turn/completed", "t1", 3)]
    result = probe.analyze_events(events, [planned()])
    assert result["turns"][0]["word_reply_matches"] is False
    assert result["turns"][0]["completed_agent_messages"][0]["withheld_text"]["utf8_bytes"] == len(secret.encode())
    assert secret not in json.dumps(result) and "privateHeader" not in json.dumps(result)
    assert secret not in json.dumps(probe.public_events(events))


def test_jsonrpc_error_message_data_and_reasoning_are_withheld_but_error_code_retained():
    secret = "Custom_gateway_auth_material_no_sk_prefix"
    events = [{"direction": "sent", "stamp": {"utc": "fake", "monotonic_ns": 0},
               "message": {"id": 1, "method": "initialize", "params": {}}},
              {"direction": "received", "stamp": {"utc": "fake", "monotonic_ns": 1},
               "message": {"id": 1, "error": {"code": -32603, "message": secret, "data": {"CUSTOM_AUTH_NAME": secret}}}},
              event("item/completed", {"threadId": "thread", "turnId": "t1", "item": {
                  "type": "reasoning", "id": "r1", "text": secret, "summary": [secret]}}, 2)]
    public = probe.public_events(events)
    assert public[1]["message"]["error"]["code"] == -32603
    assert public[2]["message"]["params"]["item"] == {"type": "reasoning", "id": "r1"}
    assert secret not in json.dumps(public) and "CUSTOM_AUTH_NAME" not in json.dumps(public)


def test_startup_error_removes_only_owned_fixture_and_keeps_artifacts(tmp_path, monkeypatch):
    observed = []

    def fail(*args, **kwargs):
        observed.append(kwargs["cwd"])
        assert not any(kwargs["cwd"].iterdir())
        assert kwargs["cwd"].parent == Path("/private/tmp").resolve()
        raise OSError("fake private startup error")

    monkeypatch.setattr(probe.subprocess, "Popen", fail)
    with tempfile.TemporaryDirectory(prefix="usage-unrelated-test-", dir="/private/tmp") as neighbor:
        marker = Path(neighbor) / "preserve.txt"
        marker.write_text("unrelated directory")
        result = probe.execute(tmp_path / "published", tmp_path / "raw", {"command": ["fake"], "turn_timeout_s": 120})
        assert marker.read_text() == "unrelated directory"
    assert len(observed) == 1 and not observed[0].exists()
    assert result["owned_fixture_cleaned"] is True and result["diagnostic_model_turns_attempted"] == 0
    assert result["raw_transcript_sha256"] is None
    assert (tmp_path / "published/result.json").exists() and (tmp_path / "raw/plan.json").exists()
    assert "fake private startup error" not in (tmp_path / "published/result.json").read_text()


def test_fixture_gate_rejects_any_git_marker_before_native_start():
    with tempfile.TemporaryDirectory(prefix="usage-git-test-", dir="/private/tmp") as directory:
        fixture = Path(directory).resolve()
        assert probe.fixture_gate(fixture)["passed"] is True
        (fixture / ".git").write_text("gitdir: ignored-external-root")
        gate = probe.fixture_gate(fixture)
        assert gate["passed"] is False and gate["checks"]["no_git_ancestor"] is False
