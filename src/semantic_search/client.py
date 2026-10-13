"""JSON client for the loopback search service."""
import json
import math
import urllib.error
import urllib.request
from http.client import HTTPException
from urllib.parse import urlsplit

MAX_RESPONSE_BYTES = 1024 * 1024


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
        data = None if payload is None else json.dumps(payload, allow_nan=False).encode()
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

    def search(self, query, **settings):
        return self._request("/search", {"query": query, **settings})

    def status(self):
        return self._request("/status")

    def refresh(self):
        return self._request("/index", {})

    def cancel(self, request_id):
        return self._request("/cancel", {"request_id": request_id})
