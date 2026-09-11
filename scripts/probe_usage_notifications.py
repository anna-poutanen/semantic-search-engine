#!/usr/bin/env python3
"""Prospective two-turn app-server usage diagnostic; default is offline planning.

Execution is intentionally separate from the frozen coding study. Only an
explicit --execute run starts app-server or sends either word-only prompt.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
OWNED_TEMP_PARENT = Path("/tmp").resolve()
MODEL, EFFORT, TIER = "gpt-6.1-sol", "ultra", "default"
PROMPTS = ("Reply exactly ALPHA. Do not use tools, files, browsing, or other agents.",
           "Reply exactly BETA. Do not use tools, files, browsing, or other agents.")
REQUIRED_USAGE = ("inputTokens", "cachedInputTokens", "outputTokens", "reasoningOutputTokens", "totalTokens")
OPTIONAL_USAGE = ("cacheWriteInputTokens",)
KNOWN_WORDS = ("ALPHA", "BETA")
KNOWN_ITEM_TYPES = ("userMessage", "agentMessage", "reasoning", "commandExecution", "fileChange",
                    "mcpToolCall", "dynamicToolCall", "collabAgentToolCall", "webSearch", "imageView",
                    "imageGeneration", "hookPrompt", "contextCompaction")
KNOWN_STATUSES = ("inProgress", "completed", "failed", "interrupted")
MISSING = object()
REMOVED_ENV = ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "CODEX_APP_TOOLS_PIPE_PATH",
               "CODEX_INTERNAL_ORIGINATOR_OVERRIDE", "CODEX_PERMISSION_PROFILE", "CODEX_SESSION_ID",
               "CODEX_TASK_WORKSPACE_VERIFYING_IDENTITY", "CODEX_THREAD_ID",
               "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE")
INSTRUCTIONS = ("This is a word-only usage-notification diagnostic. Reply exactly as each user prompt requests. "
                "Do not use tools, read files, browse, delegate, or message another agent. No tool is needed.")
OVERRIDES = {
    "model": MODEL, "model_provider": "openai", "model_reasoning_effort": EFFORT, "service_tier": TIER,
    "approval_policy": "never", "sandbox_mode": "read-only", "web_search": "disabled",
    "agents.enabled": False, "project_doc_max_bytes": 0, "project_doc_fallback_filenames": [],
    "skills.bundled.enabled": False, "skills.include_instructions": False,
    "developer_instructions": INSTRUCTIONS, "mcp_servers": {}, "plugins": {}, "hooks": {},
    "features.skip_host_skill_discovery": True,
    **{f"features.{name}": False for name in (
        "multi_agent", "multi_agent_v2", "multi_agent_v2_dynamic_tools", "collab", "collaboration_modes",
        "plugins", "remote_plugin", "apps", "hooks", "codex_hooks", "plugin_hooks", "memories",
        "browser_use", "browser_use_external", "computer_use", "image_generation", "skill_search",
        "shell_tool", "js_repl", "workspace_dependencies", "connectors", "tool_search", "tool_suggest")},
}
SCHEMAS = ("v1/InitializeParams.json", "v2/ThreadStartParams.json", "v2/ThreadStartResponse.json",
           "v2/TurnStartParams.json", "v2/TurnCompletedNotification.json",
           "v2/ThreadTokenUsageUpdatedNotification.json", "v2/GetAccountTokenUsageResponse.json")


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def stamp():
    return {"utc": datetime.now(timezone.utc).isoformat(), "monotonic_ns": time.monotonic_ns()}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n")


def toml_value(value):
    if value == {}:
        return "{}"
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def command(codex):
    return [str(codex), "app-server", "--listen", "stdio://", "--strict-config",
            *[piece for key, value in OVERRIDES.items() for piece in ("-c", f"{key}={toml_value(value)}")]]


def clean_env(environment):
    return {name: value for name, value in environment.items() if name not in REMOVED_ENV}


def redact(value, replacements=()):
    if isinstance(value, dict):
        secret_keys = {"access_token", "refresh_token", "api_key", "authorization", "id_token", "password", "bearer_token"}
        return {key: "<REDACTED>" if key.lower() in secret_keys else redact(item, replacements)
                for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, replacements) for item in value]
    if isinstance(value, str):
        for old, new in sorted(replacements, key=lambda pair: len(pair[0]), reverse=True):
            value = value.replace(old, new)
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{16,}\b", "<REDACTED_API_KEY>", value)
        return re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{24,}=*", "Bearer <REDACTED_TOKEN>", value)
    return value


def lookup(config, dotted, missing=None):
    if dotted in config:
        return config[dotted]
    current = config
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return missing
        current = current[part]
    return current


def shape(value):
    """No names, values or digests of inherited configuration are published."""
    if value is MISSING:
        return {"type": "missing", "count": None, "utf8_bytes": None, "is_missing": True, "is_null": False}
    raw = value.encode("utf-8") if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    return {"type": type(value).__name__, "count": len(value) if isinstance(value, (dict, list, str)) else None,
            "utf8_bytes": len(raw), "is_missing": False, "is_null": value is None}


def text_metadata(value):
    raw = value.encode("utf-8") if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    return {**shape(value), "sha256": sha(raw)}


def identifier(value):
    return value if isinstance(value, str) and len(value) <= 256 and re.fullmatch(r"[A-Za-z0-9_:-]+", value) else None


def public_text(value):
    return value if value in KNOWN_WORDS else {"withheld_text": text_metadata(value)}


def public_usage(value):
    if not isinstance(value, dict):
        return {"withheld_invalid_usage": shape(value)}
    output = {}
    for group in ("last", "total"):
        reported = value.get(group)
        output[group] = ({key: reported[key] for key in (*REQUIRED_USAGE, *OPTIONAL_USAGE)
                          if type(reported.get(key)) is int and reported[key] >= 0}
                         if isinstance(reported, dict) else {"withheld_invalid_breakdown": shape(reported)})
    if value.get("modelContextWindow") is None or type(value.get("modelContextWindow")) is int:
        output["modelContextWindow"] = value.get("modelContextWindow")
    return output


def public_item(item):
    kind = item.get("type")
    result = {"type": kind if kind in KNOWN_ITEM_TYPES else "unrecognized",
              "id": identifier(item.get("id"))}
    if kind == "agentMessage" and "text" in item:
        result["text"] = public_text(item["text"])
    # Reasoning text/summary fields and tool arguments/results are private.
    return result


def public_turn(value):
    if not isinstance(value, dict):
        return {"withheld_invalid_turn": shape(value)}
    result = {"id": identifier(value.get("id")),
              "status": value.get("status") if value.get("status") in KNOWN_STATUSES else None}
    for key in ("startedAt", "completedAt", "durationMs"):
        if type(value.get(key)) is int:
            result[key] = value[key]
    if isinstance(value.get("items"), list):
        result["items"] = [public_item(item) for item in value["items"] if isinstance(item, dict)]
    if value.get("error") is not None:
        result["error_shape"] = shape(value["error"])
    return result


def public_events(events):
    """Least-exposure publication; complete parsed/raw messages stay ignored."""
    request_methods = {event["message"]["id"]: event["message"]["method"] for event in events
                       if event.get("direction") == "sent" and "id" in event.get("message", {})}
    output = []
    for event in events:
        safe = {key: event[key] for key in ("direction", "channel", "stamp", "eof") if key in event}
        if "raw_utf8" in event:
            safe["raw_line_sha256"] = sha(event["raw_utf8"].encode())
            safe["raw_line_utf8_bytes"] = len(event["raw_utf8"].encode())
        if "error" in event:
            safe["stream_error_shape"] = shape(event["error"])
        message = event.get("message")
        if not isinstance(message, dict):
            output.append(safe)
            continue
        if event.get("direction") == "sent":
            # These requests are constructed exclusively by this probe. Their
            # model settings/prompts are ours; native IDs still pass validation.
            method = message.get("method")
            if method in ("initialize", "initialized", "config/read", "thread/start", "mcpServerStatus/list", "turn/start", "turn/interrupt"):
                owned = deepcopy_events(message)
                for field in ("threadId", "turnId"):
                    if field in owned.get("params", {}):
                        owned["params"][field] = identifier(owned["params"][field])
                safe["message"] = owned
            else:
                safe["message_kind"] = "withheld_unknown_sent_request"
        elif "id" in message:
            request_id = message.get("id")
            method = request_methods.get(request_id) if type(request_id) is int else None
            projected = {"id": request_id if type(request_id) is int else None}
            if "error" in message:
                error = message["error"]
                projected["error"] = {"code": error.get("code") if isinstance(error, dict) and type(error.get("code")) is int else None,
                                      "withheld_detail": shape(error)}
            else:
                result = message.get("result")
                if method == "config/read":
                    projected["result"] = {"config": "<effective config withheld; selected settings in result.json>"}
                elif method in ("turn/start", "turn/interrupt") and isinstance(result, dict):
                    projected["result"] = {"turn": public_turn(result.get("turn"))} if "turn" in result else {"response_shape": shape(result)}
                elif method == "thread/start" and isinstance(result, dict):
                    projected["result"] = {"thread": {"id": identifier(result.get("thread", {}).get("id")),
                                                       "ephemeral": result.get("thread", {}).get("ephemeral") is True},
                                           "known_model_matches": result.get("model") == MODEL,
                                           "known_effort_matches": result.get("reasoningEffort") == EFFORT,
                                           "known_tier_matches": result.get("serviceTier") == TIER,
                                           "instruction_sources_shape": shape(result.get("instructionSources"))}
                elif method == "mcpServerStatus/list" and isinstance(result, dict):
                    projected["result"] = {"server_count": len(result["data"]) if isinstance(result.get("data"), list) else None,
                                           "next_cursor_present": result.get("nextCursor") is not None}
                else:
                    projected["result"] = {"withheld_response_shape": shape(result)}
            safe["message"] = projected
        else:
            method, params = message.get("method"), message.get("params", {})
            params = params if isinstance(params, dict) else {}
            base = {key: identifier(params[key]) for key in ("threadId", "turnId", "itemId") if key in params}
            if method == "thread/tokenUsage/updated":
                safe["message"] = {"method": method, "params": {**base, "tokenUsage": public_usage(params.get("tokenUsage"))}}
            elif method in ("turn/started", "turn/completed"):
                safe["message"] = {"method": method, "params": {**base, "turn": public_turn(params.get("turn"))}}
            elif method in ("item/started", "item/completed"):
                item = params.get("item", {})
                safe["message"] = {"method": method, "params": {**base, "item": public_item(item) if isinstance(item, dict) else {"withheld_item_shape": shape(item)}}}
            elif method == "item/agentMessage/delta":
                safe["message"] = {"method": method, "params": {**base, "delta": public_text(params.get("delta"))}}
            elif method == "model/rerouted":
                safe["message"] = {"method": method, "params": {**base, "to_requested_model": params.get("toModel") == MODEL}}
            else:
                # No unknown method names, stderr strings, reasoning fragments,
                # OAuth payloads or arbitrary notification keys are copied.
                safe["message_kind"] = "withheld_unknown_notification"
                safe["payload_shape"] = shape(message)
        output.append(safe)
    return output


def config_gate(result):
    """Unknown/missing effective settings fail closed before model requests."""
    config = result.get("config", {})
    checks = {key: lookup(config, key) == value for key, value in OVERRIDES.items()}
    return {"passed": all(checks.values()), "checks": checks,
            "observed_requested_settings": {key: {"matched": checks[key],
                **({"known_requested_value": OVERRIDES[key]} if checks[key] else {"unmatched_value_shape": shape(lookup(config, key, MISSING))})}
                for key in OVERRIDES},
            "limits": ["No app-server ignore-user-config or ignore-rules option is advertised by this installed CLI.",
                       "Overrides and effective values do not prove the model-visible native tool catalog or OS read isolation.",
                       "Table overrides must be observed empty; empty requested tables alone do not establish clearing inherited entries."]}


def thread_gate(result, fixture):
    checks = {"model": result.get("model") == MODEL, "effort": result.get("reasoningEffort") == EFFORT,
              "tier": result.get("serviceTier") == TIER, "provider": result.get("modelProvider") == "openai",
              "ephemeral": result.get("thread", {}).get("ephemeral") is True,
              "instruction_sources_empty": result.get("instructionSources") == [],
              "fixture_cwd": result.get("cwd") == str(fixture),
              "read_only": result.get("sandbox", {}).get("type") == "readOnly"}
    return {"passed": all(checks.values()), "checks": checks}


def fixture_gate(fixture):
    checks = {"owned_tmp_parent": fixture.parent == OWNED_TEMP_PARENT,
              "outside_home": not fixture.is_relative_to(Path.home().resolve()),
              "outside_project": not fixture.is_relative_to(ROOT.resolve()),
              "no_git_ancestor": not any((ancestor / ".git").exists() for ancestor in (fixture, *fixture.parents)),
              "initially_empty": not any(fixture.iterdir())}
    return {"passed": all(checks.values()), "checks": checks}


def usage_breakdown(value):
    require(isinstance(value, dict), "Usage breakdown is not an object")
    for field in REQUIRED_USAGE:
        require(type(value.get(field)) is int and value[field] >= 0, "Missing/invalid reported usage field: " + field)
    for field in OPTIONAL_USAGE:
        require(field not in value or (type(value[field]) is int and value[field] >= 0), "Invalid optional usage field")
    # Cached input and reasoning output are retained as subcategories, never
    # added on top of input/output. Optional unreported cache writes stay null.
    result = {field: value.get(field) for field in (*REQUIRED_USAGE, *OPTIONAL_USAGE)}
    result["input_plus_output"] = value["inputTokens"] + value["outputTokens"]
    result["reported_total_matches_input_plus_output"] = result["input_plus_output"] == value["totalTokens"]
    result["uncached_input"] = value["inputTokens"] - value["cachedInputTokens"] if value["cachedInputTokens"] <= value["inputTokens"] else None
    return result


def analyze_events(events, turns=()):
    """Preserve every usage snapshot; never sum repeated cumulative snapshots."""
    starts, completions, snapshots, issues, items = {}, {}, [], [], []
    previous = {}
    for number, event in enumerate(events):
        message = event.get("message", {})
        if event.get("direction") != "received":
            continue
        method, params = message.get("method"), message.get("params", {})
        if method in ("turn/started", "turn/completed"):
            key = (params.get("threadId"), params.get("turn", {}).get("id"))
            (starts if method == "turn/started" else completions)[key] = (number, event)
        if method in ("item/started", "item/completed"):
            item = params.get("item", {})
            items.append({"thread_id": params.get("threadId"), "turn_id": params.get("turnId"),
                          "type": item.get("type"), "id": item.get("id"), "method": method,
                          "text": item.get("text") if item.get("type") == "agentMessage" else None})
        if method != "thread/tokenUsage/updated":
            continue
        try:
            thread, turn = params["threadId"], params["turnId"]
            require(isinstance(thread, str) and thread and isinstance(turn, str) and turn, "Usage identity missing")
            last = usage_breakdown(params["tokenUsage"]["last"])
            total = usage_breakdown(params["tokenUsage"]["total"])
        except (KeyError, TypeError, ValueError) as error:
            issues.append({"event_index": number, "error_type": type(error).__name__, "error_shape": shape(str(error)),
                           "classification": "Malformed usage notification; no replacement estimate"})
            continue
        earlier = previous.get(thread)
        duplicate = earlier is not None and params["tokenUsage"] == earlier["raw_usage"]
        delta = None
        if earlier is not None:
            differences = {field: total[field] - earlier["total"][field] for field in REQUIRED_USAGE}
            if all(value >= 0 for value in differences.values()):
                delta = differences
            else:
                issues.append({"event_index": number, "classification": "Reported cumulative counter decreased; no delta inferred"})
        snapshot = {"event_index": number, "observed_at": event["stamp"], "thread_id": thread, "turn_id": turn,
                    "last_reported": last, "cumulative_reported": total, "reported_usage_fields": public_usage(params["tokenUsage"]),
                    "duplicate_previous_snapshot": duplicate, "arithmetic_delta_from_previous_thread_snapshot": delta}
        snapshots.append(snapshot)
        previous[thread] = {"total": total, "raw_usage": params["tokenUsage"]}
    for snapshot in snapshots:
        key = (snapshot["thread_id"], snapshot["turn_id"])
        start, completion = starts.get(key), completions.get(key)
        snapshot["observed_before_turn_completed"] = snapshot["event_index"] < completion[0] if completion else None
        snapshot["observed_after_turn_started"] = snapshot["event_index"] > start[0] if start else None
    summaries = []
    for planned in turns:
        identity = (planned.get("thread_id"), planned.get("turn_id"))
        subset = [row for row in snapshots if (row["thread_id"], row["turn_id"]) == identity]
        completed = completions.get(identity)
        output = {item["id"]: item["text"] for item in items if (item["thread_id"], item["turn_id"]) == identity
                  and item["method"] == "item/completed" and item["type"] == "agentMessage"}
        status = completed[1]["message"]["params"]["turn"]["status"] if completed else None
        summaries.append({"ordinal": planned.get("ordinal"), "thread_id": identifier(planned.get("thread_id")),
                          "turn_id": identifier(planned.get("turn_id")), "attempted": planned.get("attempted") is True,
                          "expected_word": planned["expected_word"] if planned["expected_word"] in KNOWN_WORDS else None,
                          "notification_count": len(subset), "reported_last": subset[-1]["last_reported"] if subset else None,
                          "reported_thread_total": subset[-1]["cumulative_reported"] if subset else None,
                          "completed_status": status if status in KNOWN_STATUSES else None,
                          "completed_agent_messages": [public_text(text) for text in output.values()],
                          "word_reply_matches": list(output.values()) == [planned["expected_word"]] if completed else None})
    unexpected = [{"thread_id": identifier(item["thread_id"]), "turn_id": identifier(item["turn_id"]),
                   "method": item["method"], **public_item(item)} for item in items
                  if item["type"] not in ("userMessage", "agentMessage", "reasoning")]
    return {"usage_snapshots": snapshots, "turns": summaries, "issues": issues, "unexpected_items": unexpected,
            "classification": "Observed app-server usage notifications, not billing or a recovered historic receipt",
            "limits": ["last and total schema names alone establish no per-response notification cadence.",
                       "Cumulative snapshots are not added; optional fields and missing turns remain null.",
                       "Arithmetic snapshot differences are observed counter deltas, not independently identified provider responses.",
                       "Read timestamps measure observer/pipe delivery order; batching prevents true model-response timing claims.",
                       "Two word-only turns do not validate receipt coverage for tools, retries, interruption, compaction or coding workflows."]}


class JsonRpcStream:
    """Bounded line transport; readers timestamp arrivals and keep exact bytes."""
    def __init__(self, process, raw_path):
        self.process, self.events = process, []
        self.queue, self.next_id = queue.Queue(), 1
        self.raw = raw_path.open("wb")
        self.lock = threading.Lock()
        self.threads = [threading.Thread(target=self.read, args=(name, pipe), daemon=True)
                        for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr))]
        for thread in self.threads:
            thread.start()

    def read(self, channel, pipe):
        total = 0
        while True:
            raw = pipe.readline(512 * 1024 + 1)
            if not raw:
                self.queue.put({"channel": channel, "eof": True})
                return
            total += len(raw)
            if len(raw) > 512 * 1024 or total > 8 * 1024 * 1024:
                self.queue.put({"channel": channel, "error": "Bounded diagnostic stream exceeded capture cap"})
                return
            event = {"direction": "received" if channel == "stdout" else "stderr", "channel": channel,
                     "stamp": stamp(), "raw_utf8": raw.decode("utf-8", "strict")}
            if channel == "stdout":
                try:
                    event["message"] = json.loads(event["raw_utf8"])
                except ValueError as error:
                    event["error"] = "Invalid JSON stdout: " + str(error)
            self.queue.put(event)

    def record(self, event):
        self.events.append(event)
        with self.lock:
            self.raw.write((json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n").encode())
            self.raw.flush()

    def send(self, method, params, *, notification=False):
        message = {"method": method, "params": params}
        if not notification:
            message["id"] = self.next_id
            self.next_id += 1
        self.record({"direction": "sent", "stamp": stamp(), "message": message})
        self.process.stdin.write((json.dumps(message, ensure_ascii=False, allow_nan=False) + "\n").encode())
        self.process.stdin.flush()
        return message.get("id")

    def next(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Diagnostic observer deadline expired")
        try:
            event = self.queue.get(timeout=remaining)
        except queue.Empty as error:
            raise TimeoutError("Diagnostic observer deadline expired") from error
        self.record(event)
        require("error" not in event, event.get("error"))
        if event.get("eof"):
            require(event["channel"] != "stdout", "App-server stdout ended")
            return event
        message = event.get("message", {})
        require(not ("method" in message and "id" in message), "Unexpected server request; no credentials/approvals supplied")
        if message.get("method") in ("item/started", "item/completed"):
            require(message.get("params", {}).get("item", {}).get("type") in ("userMessage", "agentMessage", "reasoning"),
                    "Unexpected tool/hook/agent item; diagnostic stops")
        if message.get("method") == "model/rerouted":
            require(message.get("params", {}).get("toModel") == MODEL, "Requested model was rerouted; diagnostic stops")
        return event

    def request(self, method, params, *, timeout=30):
        request_id = self.send(method, params)
        deadline = time.monotonic() + timeout
        while True:
            message = self.next(deadline).get("message", {})
            if message.get("id") == request_id:
                require("error" not in message, "JSON-RPC request failed: " + json.dumps(message.get("error")))
                return message["result"]

    def close(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        for thread in self.threads:
            thread.join(timeout=2)
        while not self.queue.empty():
            self.record(self.queue.get_nowait())
        self.raw.close()


def plan(schema_root, codex):
    usage = json.loads((schema_root / "v2/ThreadTokenUsageUpdatedNotification.json").read_bytes())
    require(set(usage["required"]) == {"threadId", "turnId", "tokenUsage"}, "Installed usage identity schema changed")
    require(set(usage["definitions"]["ThreadTokenUsage"]["required"]) == {"last", "total"}, "Installed usage snapshot schema changed")
    version = subprocess.run([str(codex), "--version"], capture_output=True, text=True, check=True).stdout.strip()
    help_text = subprocess.run([str(codex), "app-server", "--help"], capture_output=True, text=True, check=True).stdout
    require("--ignore-user-config" not in help_text and "--ignore-rules" not in help_text,
            "Installed isolation interface changed; review protocol before running")
    return {"classification": "Prospective independent usage-observability diagnostic; not pooled with coding experiments",
            "model": MODEL, "effort": EFFORT, "service_tier": TIER, "prompts": list(PROMPTS),
            "planned_turns": 2, "turn_timeout_s": 120, "command": command(codex),
            "cli_version": version, "cli_binary_sha256": sha(Path(codex).read_bytes()),
            "app_server_help_sha256": sha(help_text.encode()),
            "schema_sha256": {name: sha((schema_root / name).read_bytes()) for name in SCHEMAS},
            "script_sha256": sha(Path(__file__).read_bytes()),
            "protocol_sha256": sha((ROOT / "docs/usage-observability-protocol.md").read_bytes()),
            "removed_environment_names": list(REMOVED_ENV), "home_auth_retained": True,
            "fixture_policy": "Owned empty directory under canonical /tmp outside Git/home ancestors; own directory removed in finally",
            "probe_reads_auth_or_user_config_files": False,
            "ignore_user_config_supported": False, "ignore_rules_supported": False,
            "requires_reviewed_config_limitations": True, "inference_executed": False}


def execute(output, raw_root, prospective):
    require(not output.exists() and not raw_root.exists(), "Refusing to overwrite/retry a diagnostic attempt")
    output.mkdir(parents=True)
    raw_root.mkdir(parents=True)
    owned_temporary = tempfile.TemporaryDirectory(prefix="semantic-usage-diagnostic-", dir=OWNED_TEMP_PARENT)
    fixture = Path(owned_temporary.name).resolve()
    stream = None
    turns = [{"ordinal": index + 1, "expected_word": word, "attempted": False,
              "thread_id": None, "turn_id": None} for index, word in enumerate(("ALPHA", "BETA"))]
    result = {"plan": prospective, "started_at": stamp(), "execution_status": "startup", "turns": turns}
    try:
        result["fixture_gate"] = fixture_gate(fixture)
        require(result["fixture_gate"]["passed"], "Owned fixture is not empty/outside Git/home ancestors; no app-server started")
        write(raw_root / "plan.json", prospective)
        write(raw_root / "fixture-private.json", {"owned_fixture_path": str(fixture), "gate": result["fixture_gate"]})
        process = subprocess.Popen(prospective["command"], cwd=fixture, env=clean_env(os.environ),
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stream = JsonRpcStream(process, raw_root / "raw-events.jsonl")
        stream.request("initialize", {"clientInfo": {"name": "semantic_usage_diagnostic", "version": "0.1.0"},
                                      "capabilities": {"experimentalApi": True, "optOutNotificationMethods": []}})
        stream.send("initialized", {}, notification=True)
        effective = stream.request("config/read", {"cwd": str(fixture), "includeLayers": False})
        result["config_gate"] = config_gate(effective)
        require(result["config_gate"]["passed"], "Effective-config isolation gate failed; no turn/start sent")
        started = stream.request("thread/start", {"model": MODEL, "modelProvider": "openai", "cwd": str(fixture),
            "approvalPolicy": "never", "sandbox": "read-only", "serviceTier": TIER, "ephemeral": True,
            "baseInstructions": INSTRUCTIONS, "developerInstructions": INSTRUCTIONS,
            "dynamicTools": [], "environments": [], "selectedCapabilityRoots": [],
            "runtimeWorkspaceRoots": [str(fixture)], "allowProviderModelFallback": False,
            "config": OVERRIDES})
        result["thread_gate"] = thread_gate(started, fixture)
        require(result["thread_gate"]["passed"], "Thread settings/instruction gate failed; no turn/start sent")
        thread_id = started["thread"]["id"]
        inventory = stream.request("mcpServerStatus/list", {"threadId": thread_id, "limit": 100, "detail": "toolsAndAuthOnly"})
        result["mcp_inventory_empty"] = inventory.get("data") == [] and inventory.get("nextCursor") is None
        require(result["mcp_inventory_empty"], "MCP inventory is not empty; no turn/start sent")
        result["execution_status"] = "turns"
        for index, prompt in enumerate(PROMPTS):
            turns[index].update({"attempted": True, "thread_id": thread_id, "sent_at": stamp()})
            deadline = time.monotonic() + prospective["turn_timeout_s"]
            response = stream.request("turn/start", {"threadId": thread_id, "input": [{"type": "text", "text": prompt}],
                "model": MODEL, "effort": EFFORT, "serviceTierForTurn": TIER, "environments": [],
                "approvalPolicy": "never", "sandboxPolicy": {"type": "readOnly", "networkAccess": False}},
                timeout=max(.001, deadline - time.monotonic()))
            turns[index]["turn_id"] = response["turn"]["id"]
            while not any(event.get("message", {}).get("method") == "turn/completed"
                          and event["message"]["params"]["threadId"] == thread_id
                          and event["message"]["params"]["turn"]["id"] == turns[index]["turn_id"] for event in stream.events):
                stream.next(deadline)
            final = analyze_events(stream.events, turns)["turns"][index]
            require(final["completed_status"] == "completed", "Turn did not complete normally; no retry/second turn")
        result["execution_status"] = "completed"
    except Exception as error:
        result["execution_status"] = "timeout" if isinstance(error, TimeoutError) else "failed_or_gate_blocked"
        result["error"] = {"type": type(error).__name__, "withheld_detail": shape(str(error))}
        # A started notification can precede the turn/start response. Preserve
        # that identity and interrupt it even when the RPC response times out.
        for turn in turns:
            if turn["attempted"] and turn["turn_id"] is None:
                known = {entry["turn_id"] for entry in turns if entry["turn_id"]}
                started_ids = [event["message"]["params"]["turn"]["id"] for event in (stream.events if stream else [])
                               if event.get("message", {}).get("method") == "turn/started"
                               and event["message"]["params"]["threadId"] == turn["thread_id"]
                               and event["message"]["params"]["turn"]["id"] not in known]
                if started_ids:
                    turn["turn_id"] = started_ids[-1]
        active = next((turn for turn in reversed(turns) if turn["attempted"] and turn["turn_id"]), None)
        if active and stream is not None:
            try:
                stream.send("turn/interrupt", {"threadId": active["thread_id"], "turnId": active["turn_id"]})
            except (OSError, ValueError):
                pass
    finally:
        try:
            if stream is not None:
                stream.close()
        except Exception as error:
            result["shutdown_error"] = {"type": type(error).__name__, "withheld_detail": shape(str(error))}
        finally:
            # TemporaryDirectory owns exactly this newly created directory;
            # output/raw artifacts and any neighboring directory are retained.
            owned_temporary.cleanup()
        result["owned_fixture_cleaned"] = not fixture.exists()
        result["finished_at"] = stamp()
        events = stream.events if stream is not None else []
        result["observations"] = analyze_events(events, turns)
        raw_transcript = raw_root / "raw-events.jsonl"
        result["raw_transcript_sha256"] = sha(raw_transcript.read_bytes()) if raw_transcript.exists() else None
        result["diagnostic_model_turns_attempted"] = sum(turn["attempted"] for turn in turns)
        result["historic_missing_receipts_replaced"] = 0
        replacements = [(str(fixture), "<EMPTY_FIXTURE>"), (str(raw_root.resolve()), "<RAW_DIAGNOSTIC>"),
                        (str(ROOT), "<PROJECT>"), (str(Path.home()), "<HOME>")]
        (output / "events.jsonl").write_text("".join(json.dumps(redact(event, replacements), ensure_ascii=False,
                                                              allow_nan=False) + "\n" for event in public_events(events)))
        write(output / "result.json", redact(result, replacements))
    return result


def deepcopy_events(events):
    return json.loads(json.dumps(events, ensure_ascii=False, allow_nan=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema-root", type=Path, default=ROOT / ".cache/usage-notification-probe/schema")
    parser.add_argument("--output", type=Path, default=ROOT / "results/usage-notifications-2026-10-13")
    parser.add_argument("--raw-root", type=Path, default=ROOT / ".cache/usage-notification-probe/run")
    parser.add_argument("--codex", type=Path, default=Path(shutil.which("codex") or "codex"))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--acknowledge-inherited-config-limitations", action="store_true")
    args = parser.parse_args()
    prospective = plan(args.schema_root, args.codex)
    if not args.execute:
        preflight = args.output.parent / (args.output.name + "-preflight")
        write(preflight / "prospective-plan.json", redact(prospective, [(str(ROOT), "<PROJECT>"), (str(Path.home()), "<HOME>")]))
        print(json.dumps({"inference_executed": False, "planned_turns": 2, "model": MODEL,
                          "isolation_limitations_require_review": True}))
        return
    require(args.acknowledge_inherited_config_limitations, "Review unsupported ignore-user-config/ignore-rules before execution")
    result = execute(args.output, args.raw_root, prospective)
    print(json.dumps({"execution_status": result["execution_status"],
                      "attempted_turns": result["diagnostic_model_turns_attempted"],
                      "usage_notifications": len(result["observations"]["usage_snapshots"])}))


if __name__ == "__main__":
    main()
