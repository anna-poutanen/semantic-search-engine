"""Optional authenticated client and owner for the isolated original B1 reranker.

Importing this module never imports MLX, downloads weights, or launches a process.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from http.client import HTTPException
import json
import math
import os
from pathlib import Path
import secrets
import signal
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import uuid

from semantic_search.client import NoRedirect, _object, _finite_float, _constant
from semantic_search.qwen_reranker_core import MODEL_ID, REVISION, MAX_TOKENS, binary_score, sha256

MAX_REPLY_BYTES = 256 * 1024
TERM_GRACE_S, KILL_GRACE_S, POLL_S = 30, 10, .05


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def positive_seconds(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive and finite")
    return value


def valid_counts(counts):
    fields = ("requests", "attempted_pairs", "completed_pairs", "attempted_forwards", "completed_forwards")
    return (isinstance(counts, dict)
        and all(type(counts.get(name)) is int and counts[name] >= 0 for name in fields)
        and counts["completed_pairs"] == counts["completed_forwards"]
        and counts["attempted_pairs"] == counts["attempted_forwards"]
        and counts["completed_pairs"] <= counts["attempted_pairs"] <= counts["requests"])


@dataclass(frozen=True)
class QwenJudgment:
    score: float  # Original yes-minus-no difference; never an H2O rubric score.
    yes_rank_signal: float
    elapsed_s: float
    input_tokens: int
    response: dict


class QwenRerankerClient:
    def __init__(self, url, token, timeout=60, *, on_failure=None):
        parsed = urlsplit(url)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
                or parsed.port == 0 or parsed.netloc.endswith(":")):
            raise ValueError("Qwen reranker URL must be a loopback HTTP base address")
        if not isinstance(token, str) or len(token) < 32 or any(char.isspace() for char in token):
            raise ValueError("Qwen reranker requires a private authentication token")
        if on_failure is not None and not callable(on_failure):
            raise ValueError("on_failure must be an owned cleanup callback")
        self._on_failure, self._failed = on_failure, False
        self.safe_for_embedding = True
        self.url, self._token = url.rstrip("/"), token
        self.timeout = positive_seconds(timeout, "timeout")
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def _fail(self, message):
        self._failed = True
        self.safe_for_embedding = False
        if self._on_failure is not None:
            receipt = self._on_failure()
            self.safe_for_embedding = bool(receipt and receipt.get("owned_group_stopped") is True)
        return RuntimeError(message)

    def _request(self, path, payload=None):
        if self._failed:
            raise RuntimeError("Qwen reranker was disabled after a failed request; restart its owner")
        raw = None if payload is None else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        request = urllib.request.Request(self.url + path, data=raw,
                    headers={"Authorization": "Bearer " + self._token, "Content-Type": "application/json"})
        try:
            with self._opener.open(request, timeout=self.timeout) as reply:
                if reply.headers.get_content_type() != "application/json":
                    raise self._fail("Qwen reranker returned a non-JSON response")
                body = reply.read(MAX_REPLY_BYTES + 1)
            if len(body) > MAX_REPLY_BYTES:
                raise self._fail("Qwen reranker response exceeded its byte limit")
            result = json.loads(body.decode(), object_pairs_hook=_object,
                                parse_float=_finite_float, parse_constant=_constant)
            if not isinstance(result, dict):
                raise ValueError("Qwen reranker response must be an object")
            return result
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            failure = RuntimeError(f"Qwen reranker returned HTTP {code}") if code == 400 else self._fail(
                f"Qwen reranker returned HTTP {code}")
            raise failure from error
        except (urllib.error.URLError, OSError, HTTPException) as error:
            raise self._fail("Cannot reach the configured local Qwen reranker") from error
        except (ValueError, UnicodeError, RecursionError) as error:
            raise self._fail("Qwen reranker returned invalid JSON") from error

    def status(self):
        result = self._request("/status")
        if (result.get("model_id") != MODEL_ID or result.get("revision") != REVISION
                or type(result.get("batch_size")) is not int or result["batch_size"] != 1
                or type(result.get("ready")) is not bool or not valid_counts(result.get("counts"))):
            raise self._fail("Qwen reranker status differs from the pinned B1 model")
        return result

    def score(self, query, snippet, *, request_id=None):
        source = snippet.text
        request_id = request_id or uuid.uuid4().hex
        started = time.perf_counter()
        result = self._request("/score", {"request_id": request_id, "query": query, "source": source})
        scored = result.get("result", {})
        if not isinstance(scored, dict):
            raise self._fail("Qwen reranker returned an invalid score object")
        if (result.get("request_id") != request_id or type(result.get("batch_size")) is not int
                or result["batch_size"] != 1 or not valid_counts(result.get("counts"))
                or scored.get("query_sha256") != sha256(query.encode())
                or scored.get("source_sha256") != sha256(source.encode())
                or type(scored.get("total_tokens")) is not int
                or not 1 <= scored["total_tokens"] <= MAX_TOKENS):
            raise self._fail("Qwen reranker result does not bind the literal request")
        try:
            observed = binary_score(scored["yes_logit"], scored["no_logit"])
            if any(type(scored[key]) not in (int, float) or not math.isfinite(scored[key])
                   or scored[key] != value for key, value in observed.items()):
                raise ValueError("Qwen score arithmetic differs")
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            raise self._fail("Qwen reranker returned an invalid score") from error
        return QwenJudgment(observed["logit_difference"], observed["yes_rank_signal"],
                            time.perf_counter() - started, scored["total_tokens"], result)


def private_file(path):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.fchmod(descriptor, 0o600)
    return os.fdopen(descriptor, "w", encoding="utf-8")


def group_operation(process, signum, group_signal, permission_receipts):
    """Reap before group operations; a live EPERM never proves disappearance."""
    process.poll()
    try:
        group_signal(process.pid, signum)
    except ProcessLookupError:
        return False
    except PermissionError as error:
        retry = {"signal": int(signum), "error_type": type(error).__name__,
                 "leader_exit_before_fresh_poll": process.returncode, "recovered": False}
        permission_receipts.append(retry)
        process.poll()
        retry["leader_exit_after_fresh_poll"] = process.returncode
        try:
            group_signal(process.pid, signum)
        except ProcessLookupError:
            retry.update(recovered=True, resolution="Owned group disappeared after leader poll/reap")
            return False
        except PermissionError:
            retry["resolution"] = "Permission error persists; disappearance unproved"
            raise
        else:
            retry.update(recovered=True, resolution="Owned group operation succeeded after poll")
    return True


def stop_owned(process, *, clock=time.monotonic, sleep=time.sleep, group_signal=os.killpg):
    receipt = {"pid": process.pid, "owned_group": process.pid, "term_sent": False,
               "forced_kill": False, "owned_group_stopped": False, "exit_code": None,
               "started_cleanup_utc": utc_now(), "errors": [], "group_permission_retries": []}

    def operation(signum):
        # Darwin's reapability can lag its initial EPERM. Retry briefly, always
        # re-polling and preserving every error; persistent live EPERM still fails.
        for attempt in range(3):
            try:
                return group_operation(process, signum, group_signal, receipt["group_permission_retries"])
            except PermissionError:
                if attempt == 2:
                    raise
                sleep(POLL_S)

    try:
        if operation(0):
            receipt["term_sent"] = operation(signal.SIGTERM)
            deadline = clock() + TERM_GRACE_S
            while operation(0) and clock() < deadline:
                sleep(POLL_S)
            if operation(0):
                receipt["forced_kill"] = operation(signal.SIGKILL)
                deadline = clock() + KILL_GRACE_S
                while operation(0) and clock() < deadline:
                    sleep(POLL_S)
        process.poll()
        if process.returncode is None:
            process.wait(timeout=KILL_GRACE_S)
        receipt["exit_code"] = process.returncode
        receipt["owned_group_stopped"] = not operation(0)
    except BaseException as error:
        receipt["errors"].append({"error_type": type(error).__name__, "error": str(error)[:200]})
    finally:
        process.poll()
        receipt["exit_code"] = process.returncode
        receipt["finished_cleanup_utc"] = utc_now()
    return receipt


class QwenRerankerProcess:
    """One explicitly owned isolated worker; never stop an external service.

    ``start()`` verifies and warms before returning ``client``. ``receipts``
    contains actual startup/provenance/counts/stop observations and no auth token.
    The private directory is retained for local diagnostics; the token is removed.
    """

    def __init__(self, python=None, checkpoint=None, *, startup_timeout=180, pair_timeout=60,
                 cache_dir=None, log_dir=None):
        root = Path(__file__).resolve().parents[2]
        self.python = Path(python or root / ".cache/reranker-experiment/.venv/bin/python").absolute()
        self.checkpoint = Path(checkpoint or root / ".cache/models/qwen3-reranker-0.6b-original").resolve()
        if cache_dir is not None and log_dir is not None:
            raise ValueError("Use only one of cache_dir or log_dir")
        self.cache_dir = Path(cache_dir or log_dir or root / ".cache/qwen-reranker-workers").resolve()
        self.startup_timeout = positive_seconds(startup_timeout, "startup_timeout")
        self.pair_timeout = positive_seconds(pair_timeout, "pair_timeout")
        self.process = self.client = self._log = None
        self.directory = self._token_path = None
        self.receipts = {"batch_size": 1, "score_caching": False, "query_caching": False}
        self._attempted = self._closed = False

    @property
    def receipt(self):
        return self.receipts

    @property
    def stop_receipt(self):
        return self.receipts.get("stop")

    @property
    def provenance(self):
        return self.receipts.get("start_status", {}).get("provenance")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.close()

    def start(self):
        if self._attempted:
            raise RuntimeError("An owned Qwen worker cannot be restarted")
        self._attempted = True
        self.receipts["started_at_utc"] = utc_now()
        self.receipts["interpreter"] = {"invocation_path": str(self.python),
                                        "resolved_binary": str(self.python.resolve())}
        started = time.monotonic()
        try:
            if not self.python.is_file() or not self.checkpoint.is_dir():
                raise ValueError("Configure an existing isolated interpreter and local checkpoint; setup is explicit")
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self.directory = Path(tempfile.mkdtemp(prefix="owned-", dir=self.cache_dir))
            self.directory.chmod(0o700)
            self._token_path = self.directory / "token.secret"
            token = secrets.token_urlsafe(48)
            with private_file(self._token_path) as stream:
                stream.write(token)
            self._log = private_file(self.directory / "worker.log")
            ready = self.directory / "ready.json"
            runtime_root = self.directory / "runtime"
            package = runtime_root / "semantic_search"
            package.mkdir(parents=True, mode=0o700)
            source_hashes = {}
            for name in ("__init__.py", "client.py", "qwen_reranker_core.py", "qwen_worker.py"):
                raw = (Path(__file__).resolve().parent / name).read_bytes()
                with private_file(package / name) as stream:
                    stream.write(raw.decode("utf-8"))
                source_hashes[name] = sha256(raw)
            self.receipts["worker_source_sha256"] = source_hashes
            env = os.environ.copy()
            env.update(PYTHONPATH=str(runtime_root), PYTHONNOUSERSITE="1",
                       PYTHONDONTWRITEBYTECODE="1")
            self.process = subprocess.Popen([str(self.python), "-B", "-m", "semantic_search.qwen_worker",
                    "--checkpoint", str(self.checkpoint), "--state-dir", str(self.directory),
                    "--owner-pid", str(os.getpid())],
                    cwd=self.directory, env=env, stdout=self._log, stderr=subprocess.STDOUT,
                    start_new_session=True)
            self.receipts.update(pid=self.process.pid, owned_group=self.process.pid,
                                 private_directory_mode=oct(self.directory.stat().st_mode & 0o777),
                                 token_mode=oct(self._token_path.stat().st_mode & 0o777))
            deadline = started + self.startup_timeout
            while not ready.exists():
                if self.process.poll() is not None:
                    raise RuntimeError("Owned Qwen worker exited before readiness; inspect its private log")
                if time.monotonic() >= deadline:
                    raise TimeoutError("Owned Qwen worker readiness timed out")
                time.sleep(POLL_S)
            if time.monotonic() >= deadline:
                raise TimeoutError("Owned Qwen worker readiness timed out")
            record = json.loads(ready.read_text())
            if (record.get("pid") != self.process.pid or record.get("process_group") != self.process.pid
                    or type(record.get("owner_pid")) is not int or record["owner_pid"] != os.getpid()
                    or type(record.get("batch_size")) is not int or record["batch_size"] != 1
                    or record.get("ready") is not True):
                raise RuntimeError("Owned Qwen readiness does not bind the launched worker")
            self.client = QwenRerankerClient(record["url"], token, self.pair_timeout, on_failure=self.close)
            self.client.timeout = min(self.pair_timeout, max(.001, deadline - time.monotonic()))
            status = self.client.status()
            self.client.timeout = self.pair_timeout
            if time.monotonic() >= deadline:
                raise TimeoutError("Owned Qwen worker readiness timed out")
            if status.get("ready") is not True or status.get("pid") != self.process.pid:
                raise RuntimeError("Owned Qwen worker is not ready")
            self.receipts.update(startup_s=time.monotonic() - started, ready=record, start_status=status)
            return self.client
        except BaseException as error:
            self.receipts["startup_error"] = {"error_type": type(error).__name__, "error": str(error)[:256]}
            self.close()
            raise

    def status(self):
        if self.client is None or self._closed:
            raise RuntimeError("Owned Qwen worker is not running")
        result = self.client.status()
        self.receipts["latest_status"] = result
        return result

    def close(self):
        if self._closed:
            return self.receipts.get("stop")
        self._closed = True
        try:
            if self.process is not None:
                self.receipts["stop"] = stop_owned(self.process)
        finally:
            errors = self.receipts.setdefault("private_cleanup_errors", [])
            if self._token_path is not None:
                try:
                    self._token_path.unlink(missing_ok=True)
                except OSError as error:
                    errors.append({"operation": "remove_auth_token", "error_type": type(error).__name__})
            self.receipts["auth_token_removed"] = self._token_path is None or not self._token_path.exists()
            self.client = None
            if self._log is not None:
                try:
                    self._log.close()
                except OSError as error:
                    errors.append({"operation": "close_private_log", "error_type": type(error).__name__})
                self._log = None
            if self.directory is not None:
                stop_path = self.directory / "stop.json"
                if stop_path.is_file():
                    try:
                        self.receipts["worker_stop"] = json.loads(stop_path.read_text())
                    except (OSError, ValueError) as error:
                        errors.append({"operation": "read_worker_stop", "error_type": type(error).__name__})
            self.receipts["closed_at_utc"] = utc_now()
        return self.receipts.get("stop")
