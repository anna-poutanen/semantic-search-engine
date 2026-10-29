"""Loopback-only JSON HTTP transport for a persistent SearchBackend."""
from __future__ import annotations

import ipaddress
import json
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from semantic_search.backend import SourceSnapshotError
from semantic_search.cancellation import SearchCancelled
from semantic_search.overlays import MAX_OVERLAY_BODY_BYTES

MAX_BODY_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024


class BackendHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = True

    def __init__(self, address, backend):
        self.backend = backend
        super().__init__(address, BackendHandler)

    def server_close(self):
        try:
            super().server_close()
        finally:
            self.backend.close()


class BackendHandler(BaseHTTPRequestHandler):
    server_version = "SemanticSearch/0.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format, *args):
        # Queries and paths should not be copied into daemon access logs.
        pass

    def _send(self, status, data):
        payload = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
        if len(payload) > MAX_RESPONSE_BYTES:
            status = 500
            payload = b'{"error":{"code":"response_limit","message":"Response exceeds size limit"}}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self.wfile.write(payload)

    def _error(self, status, code, message):
        self._send(status, {"error": {"code": code, "message": message[:1000]}})

    def _guard(self):
        if not ipaddress.ip_address(self.client_address[0]).is_loopback:
            self._error(403, "loopback_required", "Only loopback clients are allowed")
            return False
        hosts = self.headers.get_all("Host", [])
        port = self.server.server_address[1]
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
        bound = self.server.server_address[0]
        allowed.add(f"[{bound}]:{port}" if ":" in bound else f"{bound}:{port}")
        if len(hosts) != 1 or hosts[0].lower() not in allowed:
            self._error(403, "invalid_host", "Host must identify this loopback server and port")
            return False
        if (self.headers.get_all("Origin") or self.headers.get_all("Referer")
                or any(name.lower().startswith("sec-fetch-") for name in self.headers)):
            self._error(403, "browser_request", "Browser-origin requests are not allowed")
            return False
        if self.headers.get_all("Transfer-Encoding"):
            self._error(400, "invalid_body", "Transfer-Encoding is unsupported")
            return False
        return True

    @staticmethod
    def _object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON keys are not allowed")
            result[key] = value
        return result

    @staticmethod
    def _constant(value):
        raise ValueError("Nonfinite JSON values are invalid")

    def _body(self):
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
            self._error(411, "content_length_required", "One valid Content-Length is required")
            return None
        if len(lengths[0]) > 20:
            self._error(413, "body_limit", "Content-Length exceeds size limit")
            return None
        size = int(lengths[0])
        transport_limit = MAX_OVERLAY_BODY_BYTES if self.path == "/search" else MAX_BODY_BYTES
        if size > transport_limit:
            self._error(413, "body_limit", f"JSON body exceeds {transport_limit} bytes")
            return None
        types = self.headers.get_all("Content-Type", [])
        if len(types) != 1 or types[0].split(";", 1)[0].strip().lower() != "application/json":
            self._error(415, "content_type", "Content-Type must be application/json")
            return None
        try:
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise ValueError("Incomplete request body")
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=self._object,
                              parse_constant=self._constant)
            if not isinstance(data, dict):
                raise ValueError("JSON body must be an object")
            if size > MAX_BODY_BYTES and not (self.path == "/search" and isinstance(data.get("overlays"), list)
                                             and data["overlays"]):
                self._error(413, "body_limit", "Saved-source JSON body exceeds 65536 bytes")
                return None
            return data
        except (ValueError, UnicodeError, TimeoutError, RecursionError) as error:
            if size > MAX_BODY_BYTES:
                self._error(413, "body_limit", "Oversized body must contain an overlay search")
            else:
                self._error(400, "invalid_json", str(error))
            return None

    def do_GET(self):
        if not self._guard():
            return
        if self.path not in ("/health", "/status"):
            self._error(404, "not_found", "Unknown endpoint")
            return
        try:
            status = self.server.backend.status()
            if self.path == "/health":
                self._send(200 if not status["closed"] else 503, {"ok": not status["closed"]})
            else:
                self._send(200, status)
        except Exception as error:
            self._error(503, "backend_error", f"Search backend status failed: {error}")

    def do_POST(self):
        if not self._guard():
            return
        if self.path not in ("/search", "/index", "/cancel"):
            self._error(404, "not_found", "Unknown endpoint")
            return
        data = self._body()
        if data is None:
            return
        try:
            if self.path == "/cancel":
                if set(data) != {"request_id"}:
                    raise ValueError("/cancel requires exactly request_id")
                result = self.server.backend.cancel(data["request_id"])
            elif self.path == "/index":
                if data:
                    raise ValueError("/index accepts an empty JSON object")
                result = {"indexstats": self.server.backend.refresh()}
            else:
                allowed = {"query", "retrieval", "rerank", "candidates", "max_results", "source_budget",
                           "token_budget", "include_dependencies", "request_id", "overlays", "rerank_provider"}
                if set(data) - allowed:
                    raise ValueError("Unknown search option")
                if "query" not in data:
                    raise ValueError("query is required")
                result = self.server.backend.search(**data)
            self._send(200, result)
        except ValueError as error:
            self._error(400, "invalid_request", str(error))
        except SourceSnapshotError as error:
            self._error(409, "source_snapshot", str(error))
        except SearchCancelled as error:
            self._error(409, "request_cancelled", str(error))
        except Exception as error:
            self._error(503, "backend_error", f"Search backend failed: {error}")

    def do_OPTIONS(self):
        if self._guard():
            self._error(405, "method_not_allowed", "Only GET and POST are supported")


def create_server(backend, host="127.0.0.1", port=8750):
    """Create an unstarted local server; closing the server closes its backend."""
    if host == "localhost":
        host = "127.0.0.1"
    try:
        if not ipaddress.ip_address(host).is_loopback:
            raise ValueError("Server host must be a loopback address")
    except ValueError as error:
        raise ValueError("Server host must be localhost or a loopback IP address") from error
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("Server port must be an integer from 0 to 65535")
    if ":" in host:
        class IPv6BackendHTTPServer(BackendHTTPServer):
            address_family = socket.AF_INET6

        return IPv6BackendHTTPServer((host, port), backend)
    return BackendHTTPServer((host, port), backend)


def serve_backend(backend, host="127.0.0.1", port=8750):
    server = create_server(backend, host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
