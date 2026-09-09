"""JSON client for the loopback search service."""
import json
import math
import urllib.error
import urllib.request
from http.client import HTTPException
from urllib.parse import urlsplit

MAX_RESPONSE_BYTES = 1024 * 1024


class StaleBufferError(RuntimeError):
    """A response's overlay inputs no longer match the editor's current buffers."""


def ensure_overlay_current(response, current_buffers):
    """Check all input buffers, even when results contain only saved source.

    current_buffers maps paths to {version, content_hash}. Call again immediately
    before editor navigation; a transport callback checks only response delivery.
    """
    scope = response.get("scope")
    if not isinstance(scope, dict) or scope.get("source_state") != "overlay":
        raise RuntimeError("Search response lacks overlay provenance")
    bindings = scope.get("overlays")
    if not isinstance(bindings, list) or not isinstance(current_buffers, dict):
        raise RuntimeError("Overlay response/current buffer bindings are invalid")
    from semantic_search.overlays import HASH_PATTERN, MAX_OVERLAY_FILES
    if not 1 <= len(bindings) <= MAX_OVERLAY_FILES:
        raise RuntimeError("Overlay response has invalid buffer bindings")
    seen = set()
    for binding in bindings:
        if (not isinstance(binding, dict) or set(binding) != {"path", "version", "content_hash"}
                or not isinstance(binding["path"], str) or binding["path"] in seen
                or type(binding["version"]) is not int or binding["version"] < 0
                or not isinstance(binding["content_hash"], str) or not HASH_PATTERN.fullmatch(binding["content_hash"])):
            raise RuntimeError("Overlay response has invalid buffer bindings")
        seen.add(binding["path"])
        current = current_buffers.get(binding["path"])
        if (not isinstance(current, dict) or type(current.get("version")) is not int
                or current.get("version") != binding["version"]
                or current.get("content_hash") != binding["content_hash"]):
            raise StaleBufferError(f"Overlay result is stale for {binding['path']}; issue a new search")
    return response


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON keys")
        result[key] = value
    return result


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Nonfinite JSON number")
    return number


def _constant(value):
    raise ValueError("Nonfinite JSON number")


def _nonnegative_integer(value):
    return type(value) is int and value >= 0


def _valid_result(row):
    if not isinstance(row, dict) or any(not isinstance(row.get(key), str)
                                        for key in ("path", "symbol", "source")):
        return False
    if (type(row.get("start_line")) is not int or type(row.get("end_line")) is not int
            or not 1 <= row["start_line"] <= row["end_line"]):
        return False
    for key in ("h2o_score", "retrieval_score"):
        score = row.get(key)
        if score is not None:
            if type(score) not in (int, float):
                return False
            try:
                if not math.isfinite(score):
                    return False
            except OverflowError:
                return False
    if "source_origin" in row:
        if not isinstance(row["source_origin"], str) or row["source_origin"] not in {"saved", "overlay"}:
            return False
        if row["source_origin"] == "overlay":
            from semantic_search.overlays import HASH_PATTERN
            if (type(row.get("buffer_version")) is not int or row["buffer_version"] < 0
                    or not isinstance(row.get("buffer_hash"), str) or not HASH_PATTERN.fullmatch(row["buffer_hash"])):
                return False
    return "truncated" not in row or type(row["truncated"]) is bool


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        raise urllib.error.HTTPError(request.full_url, code,
                                     "Loopback service redirects are not allowed", headers, fp)


class SearchClient:
    def __init__(self, url="http://127.0.0.1:8750", timeout=180):
        parsed = urlsplit(url)
        port = parsed.port
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
                or port == 0 or parsed.netloc.endswith(":")):
            raise ValueError("Search service URL must be a loopback HTTP address without a path")
        self.url = f"http://{parsed.hostname}:{port if port is not None else 80}"
        self.timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def _request(self, path, payload=None):
        data = None if payload is None else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        request = urllib.request.Request(self.url + path, data=data,
                                         headers={"Content-Type": "application/json"} if data else {})
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                if response.headers.get_content_type() != "application/json":
                    raise RuntimeError("Search service did not return application/json")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise RuntimeError("Search service response exceeds the 1 MiB limit")
            try:
                result = json.loads(raw.decode("utf-8"), object_pairs_hook=_object,
                                    parse_float=_finite_float, parse_constant=_constant)
            except (ValueError, UnicodeError, RecursionError) as error:
                raise RuntimeError("Search service returned invalid JSON") from error
            if not isinstance(result, dict):
                raise RuntimeError("Search service returned an unexpected response")
            if path == "/search" and (result.get("query") != payload["query"]
                    or not isinstance(result.get("results"), list)
                    or any(not _valid_result(row) for row in result["results"])):
                raise RuntimeError("Search service returned an unexpected search response")
            if path == "/status" and (type(result.get("closed")) is not bool
                                      or not _nonnegative_integer(result.get("windows"))):
                raise RuntimeError("Search service returned an unexpected status response")
            if path == "/index" and (not isinstance(result.get("indexstats"), dict)
                    or not _nonnegative_integer(result["indexstats"].get("indexed_windows"))):
                raise RuntimeError("Search service returned an unexpected index response")
            return result
        except urllib.error.HTTPError as error:
            try:
                detail = error.read(4096).decode("utf-8", errors="replace")
            finally:
                error.close()
            if 300 <= error.code < 400:
                raise RuntimeError(f"Search service redirects are not allowed (HTTP {error.code})") from error
            raise RuntimeError(f"Search service returned HTTP {error.code}: {detail}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"Cannot reach {self.url}; start semantic-search serve first") from error
        except (OSError, HTTPException) as error:
            raise RuntimeError(f"Could not read the search service response: {error}") from error

    def search(self, query, *, overlays=None, current_buffers=None, **settings):
        from semantic_search.overlays import normalize_overlays
        entries = normalize_overlays(overlays)
        if current_buffers is not None and (not callable(current_buffers) or not entries):
            raise ValueError("current_buffers must be a callback for a nonempty overlay request")
        payload = {"query": query, **settings}
        if overlays is not None:
            payload["overlays"] = [{**entry.binding, "text": entry.text} for entry in entries]
        response = self._request("/search", payload)
        if entries:
            expected = {entry.path: {"version": entry.version, "content_hash": entry.content_hash} for entry in entries}
            ensure_overlay_current(response, expected)
            bindings = response["scope"]["overlays"]
            if {item["path"] for item in bindings} != set(expected):
                raise RuntimeError("Overlay response did not bind every request buffer")
            for row in response["results"]:
                entry = expected.get(row["path"])
                if entry is not None:
                    if (row.get("source_origin") != "overlay" or row.get("buffer_version") != entry["version"]
                            or row.get("buffer_hash") != entry["content_hash"]):
                        raise RuntimeError("Overlay response has inconsistent source provenance")
                elif row.get("source_origin") != "saved":
                    raise RuntimeError("Overlay response has inconsistent saved provenance")
            if current_buffers is not None:
                ensure_overlay_current(response, current_buffers())
        return response

    def status(self):
        return self._request("/status")

    def refresh(self):
        return self._request("/index", {})

    def cancel(self, request_id):
        return self._request("/cancel", {"request_id": request_id})
