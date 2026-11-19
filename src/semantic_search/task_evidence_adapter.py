"""Explicit request binding for the research-only task-evidence experiment.

The adapter is deliberately not part of the HTTP, MCP, CLI, or VS Code
contracts. A research harness installs the existing postprocessor on one
backend instance, then binds one fully resolved request in an isolated process.
During that binding only, the adapter replaces the backend module's imported
``select_context`` alias and restores it in ``finally``. A process-wide lock,
strict identity checks, and context-local state make the patch fail closed.

Because the selector alias is process-global while a request executes, this
adapter is for a dedicated single-experiment process, not an in-process service.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
from threading import RLock
import time
from typing import Any, Callable

from semantic_search import backend as backend_module
from semantic_search import selection as selection_module
from semantic_search.context import compact_result, serialize_context
from semantic_search.context_backfill import coalesce_context
from semantic_search.selection import definition_context


NEEDED_EVIDENCE = ("implementation", "callers", "tests", "behavioral_constraints")
_SELECTOR_PATCH_LOCK = RLock()
_BACKEND_REQUEST_FIELDS = frozenset({
    "query", "retrieval", "rerank", "rerank_provider", "candidates",
    "max_results", "source_budget", "token_budget", "include_dependencies",
    "overlays",
})


def _integer(value: object, name: str, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}")


def _validate_request(value: object) -> tuple[dict[str, Any], tuple[str, ...] | None]:
    """Validate a resolved research request without accepting hidden inputs."""
    if type(value) is not dict:
        raise TypeError("resolved_request must be a plain dictionary")
    fields = set(value)
    treatment = "needed_evidence" in value
    expected = _BACKEND_REQUEST_FIELDS | ({"needed_evidence"} if treatment else set())
    if fields != expected:
        missing, extra = sorted(expected - fields), sorted(fields - expected)
        raise ValueError(f"resolved_request fields differ (missing={missing}, extra={extra})")

    query = value["query"]
    if not isinstance(query, str) or not query.strip() or len(query) > 8192:
        raise ValueError("query must be nonempty and at most 8192 characters")
    try:
        query.encode("utf-8")
    except UnicodeError as error:
        raise ValueError("query must contain valid Unicode text") from error
    if value["retrieval"] not in ("bm25", "dense", "hybrid", "multifile"):
        raise ValueError("retrieval is invalid")
    if type(value["rerank"]) is not bool:
        raise ValueError("rerank must be a boolean")
    if value["rerank_provider"] not in ("h2o", "qwen"):
        raise ValueError("rerank_provider is invalid")
    _integer(value["candidates"], "candidates", 1, 50)
    _integer(value["max_results"], "max_results", 1, 20)
    _integer(value["source_budget"], "source_budget", 0, 100_000)
    _integer(value["token_budget"], "token_budget", 512, 32_000)
    if type(value["include_dependencies"]) is not bool:
        raise ValueError("include_dependencies must be a boolean")
    if type(value["overlays"]) is not list:
        raise ValueError("overlays must be a list")

    requested = None
    if treatment:
        raw = value["needed_evidence"]
        if type(raw) is not list or not raw:
            raise ValueError("needed_evidence must be a nonempty list")
        if any(type(item) is not str or item not in NEEDED_EVIDENCE for item in raw):
            raise ValueError("needed_evidence contains an unsupported value")
        if len(set(raw)) != len(raw):
            raise ValueError("needed_evidence must not contain duplicates")
        requested = tuple(raw)

    try:
        copied = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, UnicodeError) as error:
        raise ValueError("resolved_request must contain finite JSON data") from error
    backend_request = {key: copied[key] for key in copied if key != "needed_evidence"}
    return backend_request, requested


@dataclass
class _RunState:
    backend_request: dict[str, Any]
    requested: tuple[str, ...] | None
    selector_calls: int = 0
    postprocessor_calls: int = 0
    selection_receipt: dict[str, Any] | None = None
    packing_receipt: dict[str, Any] | None = None
    packet: dict[str, Any] | None = None
    standard_compact: dict[str, Any] | None = None
    capture_record: dict[str, Any] | None = None
    candidate_inventory: list[dict[str, Any]] | None = None
    request_receipt: dict[str, Any] | None = None
    backend_search_s: float | None = None
    execution_started: bool = False
    execution_failed: bool = False
    finished: bool = False


@dataclass(frozen=True)
class _TaskEvidenceRun:
    """One bound condition. Returned values are defensive copies."""

    _adapter: "TaskEvidenceAdapter" = field(repr=False)
    _state: _RunState = field(repr=False)

    @property
    def backend_request(self) -> dict[str, Any]:
        return deepcopy(self._state.backend_request)

    def _execute(self, backend) -> tuple[dict[str, Any], dict[str, Any]]:
        """Run the exact bound backend request and return response plus packet."""
        return self._adapter._execute(self._state, backend)


class TaskEvidenceAdapter:
    """Bind one exact research request to isolated selector/packer hooks."""

    def __init__(self, capture: Callable[[dict[str, Any]], None] | None = None):
        if capture is not None and not callable(capture):
            raise TypeError("capture must be callable")
        self._capture = capture
        self._active: ContextVar[_RunState | None] = ContextVar(
            f"task_evidence_run_{id(self)}", default=None)

    def _state(self) -> _RunState:
        state = self._active.get()
        if state is None:
            raise RuntimeError("Task-evidence hooks require an active bound request")
        return state

    def execute(self, backend, resolved_request: object) -> tuple[dict[str, Any], dict[str, Any]]:
        """Execute and finalize one condition after all integrity cleanup passes."""
        with self._bind(resolved_request) as run:
            result = run._execute(backend)
        return result

    @contextmanager
    def _bind(self, resolved_request: object):
        backend_request, requested = _validate_request(resolved_request)
        if self._active.get() is not None:
            raise RuntimeError("Task-evidence requests cannot be nested on one adapter")
        state = _RunState(backend_request=backend_request, requested=requested)
        hook = self.select_context
        capture_record = None
        with _SELECTOR_PATCH_LOCK:
            expected = selection_module.definition_context
            if backend_module.select_context is not expected:
                raise RuntimeError(
                    "Task-evidence selector requires the unmodified backend alias")
            backend_module.select_context = hook
            token = self._active.set(state)
            active_error: BaseException | None = None
            try:
                try:
                    yield _TaskEvidenceRun(self, state)
                except BaseException as error:
                    active_error = error
                    raise
                if not state.finished:
                    active_error = RuntimeError(
                        "Task-evidence binding exited without executing its request")
                    raise active_error
            finally:
                tampered = backend_module.select_context is not hook
                backend_module.select_context = expected
                self._active.reset(token)
                if tampered:
                    message = "Task-evidence selector alias changed during a bound request"
                    if active_error is not None:
                        active_error.add_note(message)
                    else:
                        raise RuntimeError(message)
            if state.capture_record is None:
                raise RuntimeError("Task-evidence execution has no capture record")
            capture_record = deepcopy(state.capture_record)
        if self._capture is not None:
            self._capture(capture_record)

    def _execute(self, state: _RunState, backend) -> tuple[dict[str, Any], dict[str, Any]]:
        if self._active.get() is not state:
            raise RuntimeError("execute must run inside its active request binding")
        if state.execution_started:
            raise RuntimeError("Task-evidence request execution was already attempted")
        if not isinstance(backend, backend_module.SearchBackend):
            raise TypeError("backend must be a SearchBackend")
        processor = getattr(backend, "_research_postprocessor", None)
        if (getattr(processor, "__self__", None) is not self
                or getattr(processor, "__func__", None)
                is not self.postprocess_context.__func__):
            raise ValueError("backend is not installed with this task-evidence adapter")
        state.execution_started = True
        try:
            started = time.monotonic()
            try:
                response = backend.search(**deepcopy(state.backend_request))
            finally:
                state.backend_search_s = time.monotonic() - started
            packet = self._finish(state, response)
        except BaseException:
            state.execution_failed = True
            raise
        return response, packet

    def select_context(self, pairs, *, query, max_results, include_dependencies,
                       references, definition_keys=None, ambiguous_symbols=None):
        state = self._state()
        if not state.execution_started or state.execution_failed:
            raise RuntimeError("Task-evidence selector requires TaskEvidenceAdapter.execute")
        expected = state.backend_request
        if (query != expected["query"] or max_results != expected["max_results"]
                or include_dependencies is not expected["include_dependencies"]):
            raise ValueError("Selector arguments differ from the bound backend request")
        state.selector_calls += 1
        if state.selector_calls != 1:
            raise RuntimeError("Task-evidence selector may run only once per request")
        pairs = list(pairs)
        state.candidate_inventory = [
            {
                "id": item.id,
                "path": item.path,
                "content_hash": item.content_hash,
                "retrieval_score": float(score),
            }
            for item, score in pairs
        ]
        kwargs = {
            "query": query,
            "max_results": max_results,
            "include_dependencies": include_dependencies,
            "references": references,
            "definition_keys": definition_keys,
            "ambiguous_symbols": ambiguous_symbols,
        }
        if state.requested is None:
            return definition_context(pairs, **kwargs)

        from semantic_search.task_evidence import task_evidence_context

        selected, eligible, origins = task_evidence_context(
            pairs, needed_evidence=list(state.requested), **kwargs)
        receipt = getattr(eligible, "source_plan", None)
        if not isinstance(receipt, dict):
            raise ValueError("Task-evidence selection did not return its planning receipt")
        if receipt.get("requested") != list(state.requested):
            raise ValueError("Task-evidence planning receipt does not match the bound request")
        state.selection_receipt = deepcopy(receipt)
        return selected, eligible, origins

    def postprocess_context(self, response, candidates, *, max_results, source_budget,
                            token_budget=None, cancel_check=lambda: None,
                            ranked_candidates=None, definition_keys=None,
                            reference_factory=None, selection_plan=None, **research):
        state = self._state()
        if not state.execution_started or state.execution_failed:
            raise RuntimeError(
                "Task-evidence postprocessor requires TaskEvidenceAdapter.execute")
        expected = state.backend_request
        request_context = research.get("request_context")
        if (max_results != expected["max_results"]
                or source_budget != expected["source_budget"]
                or token_budget != expected["token_budget"]
                or not isinstance(request_context, dict)
                or request_context.get("query") != expected["query"]
                or request_context.get("retrieval") != expected["retrieval"]
                or not isinstance(response, dict)
                or response.get("query") != expected["query"]
                or response.get("retrieval") != expected["retrieval"]
                or response.get("rerank") is not expected["rerank"]
                or response.get("rerank_provider") != expected["rerank_provider"]
                or response.get("source_budget") != expected["source_budget"]):
            raise ValueError("Packing arguments differ from the bound backend request")
        usage = response.get("token_usage")
        if (not isinstance(usage, dict)
                or usage.get("budget") != expected["token_budget"]):
            raise ValueError("Packing token budget differs from the bound backend request")
        vector = request_context.get("query_vector")
        vector_bytes = vector.tobytes(order="C") if vector is not None else None
        state.request_receipt = {
            "source_snapshot": request_context.get("source_snapshot"),
            "model_fingerprint": request_context.get("model_fingerprint"),
            "query_vector": ({
                "dtype": str(vector.dtype),
                "shape": list(vector.shape),
                "sha256": hashlib.sha256(vector_bytes).hexdigest(),
            } if vector is not None else None),
        }
        if state.selector_calls != 1:
            raise RuntimeError("Task-evidence postprocessor requires one selector call")
        state.postprocessor_calls += 1
        if state.postprocessor_calls != 1:
            raise RuntimeError("Task-evidence postprocessor may run only once per request")

        if state.requested is None:
            if selection_plan is not None:
                raise ValueError("Control selection unexpectedly returned a research plan")
            standard = coalesce_context(
                response, candidates, max_results=max_results,
                source_budget=source_budget, token_budget=token_budget,
                cancel_check=cancel_check, ranked_candidates=ranked_candidates,
                definition_keys=definition_keys, reference_factory=reference_factory)
            state.standard_compact = deepcopy(compact_result(standard))
            state.packet = deepcopy(state.standard_compact)
            state.packing_receipt = None
            return standard

        if selection_plan != state.selection_receipt:
            raise ValueError("Backend did not forward the bound task-evidence selection plan")
        from semantic_search.task_evidence import pack_task_evidence

        standard, packet, receipt = pack_task_evidence(
            response, candidates, selection_plan=selection_plan,
            ranked_candidates=ranked_candidates, reference_factory=reference_factory,
            max_results=max_results, source_budget=source_budget,
            token_budget=token_budget, cancel_check=cancel_check,
            definition_keys=definition_keys, **research)
        if not all(isinstance(value, dict) for value in (standard, packet, receipt)):
            raise ValueError("Task-evidence packing must return response, packet, and receipt objects")
        if "task_evidence" in standard or "task_evidence" not in packet:
            raise ValueError("Task-evidence metadata must exist only in the research packet")
        if packet["task_evidence"].get("requested") != list(state.requested):
            raise ValueError("Task-evidence packet does not match the bound request")
        state.standard_compact = deepcopy(compact_result(standard))
        state.packet = deepcopy(packet)
        state.packing_receipt = deepcopy(receipt)
        return standard

    def _finish(self, state: _RunState, response: object) -> dict[str, Any]:
        if self._active.get() is not state:
            raise RuntimeError("finish must run inside its active request binding")
        if not state.execution_started or state.execution_failed:
            raise RuntimeError("finish requires a successful bound execution")
        if state.finished:
            raise RuntimeError("Task-evidence request was already finished")
        if state.selector_calls != 1 or state.postprocessor_calls != 1 or state.packet is None:
            raise RuntimeError("Task-evidence request did not complete selection and packing")
        if (state.candidate_inventory is None or state.request_receipt is None
                or state.backend_search_s is None):
            raise RuntimeError("Task-evidence execution receipts are incomplete")
        if not isinstance(response, dict):
            raise TypeError("response must be a dictionary")

        actual = compact_result(response)
        expected = state.standard_compact
        if expected is None:
            raise RuntimeError("Task-evidence standard response was not retained")
        # The treatment packet owns the complete-envelope token count. Depending
        # on the packer, the standard internal response may retain a context-only
        # count, so compare all other compact fields literally.
        comparable_actual, comparable_expected = deepcopy(actual), deepcopy(expected)
        comparable_actual.get("token_usage", {}).pop("tokens", None)
        comparable_expected.get("token_usage", {}).pop("tokens", None)
        if comparable_actual != comparable_expected:
            raise ValueError("Final backend response differs from the packed standard response")
        if state.requested is None:
            if serialize_context(actual) != serialize_context(state.packet):
                raise ValueError("Control packet is not byte-identical to the final compact response")
        else:
            base = deepcopy(state.packet)
            base.pop("task_evidence", None)
            comparable_packet = deepcopy(base)
            comparable_packet.get("token_usage", {}).pop("tokens", None)
            if comparable_packet != comparable_actual:
                raise ValueError("Treatment packet source differs from the final backend response")

        state.finished = True
        packet = deepcopy(state.packet)
        record = {
            "backend_request": deepcopy(state.backend_request),
            "needed_evidence": (list(state.requested) if state.requested is not None else None),
            "packet": deepcopy(packet),
            "selection_receipt": deepcopy(state.selection_receipt),
            "packing_receipt": deepcopy(state.packing_receipt),
            "candidate_inventory": deepcopy(state.candidate_inventory),
            "request_receipt": deepcopy(state.request_receipt),
            "backend_search_s": state.backend_search_s,
        }
        state.capture_record = record
        return packet


__all__ = ["NEEDED_EVIDENCE", "TaskEvidenceAdapter"]
