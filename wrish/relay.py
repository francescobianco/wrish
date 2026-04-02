from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import base64
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import threading
import time
from typing import Any
from urllib import error, parse, request

from ._sentinel import (
    SENTINEL_DIAGNOSIS_INTERVAL,
    maybe_cycle_sentinel_adapter,
)
from .concurrency import BleLockBusyError, ble_session
from .devices.c60_a82c import C60A82CDevice, DeviceError


def _coerce_text(value: bytes) -> tuple[str, bool]:
    try:
        return value.decode("utf-8"), False
    except UnicodeDecodeError:
        return base64.b64encode(value).decode("ascii"), True


def _decode_payload(body: str, is_base64: bool) -> bytes:
    if is_base64:
        return base64.b64decode(body.encode("ascii"))
    return body.encode("utf-8")


def _ensure_trailing_newline(payload: bytes, content_type: str) -> bytes:
    lowered = content_type.lower()
    is_textual = (
        lowered.startswith("text/")
        or "json" in lowered
        or "xml" in lowered
        or "javascript" in lowered
        or lowered == ""
    )
    if is_textual and not payload.endswith(b"\n"):
        return payload + b"\n"
    return payload


def _filtered_headers(headers: dict[str, str]) -> dict[str, str]:
    excluded = {
        "host",
        "connection",
        "content-length",
        "transfer-encoding",
        "accept-encoding",
    }
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in excluded
    }


@dataclass(slots=True)
class RelayContext:
    mac: str
    hci: str
    debug: bool
    lock: threading.Lock

    def device(self) -> C60A82CDevice:
        return C60A82CDevice(mac=self.mac, hci=self.hci, debug=self.debug)

    @contextmanager
    def ble_session(self, *, blocking: bool, reason: str):
        with self.lock:
            with ble_session(blocking=blocking, reason=reason):
                yield


class LocalCommandHandler(BaseHTTPRequestHandler):
    server_version = "wrish-relay/0.1"

    @property
    def context(self) -> RelayContext:
        return self.server.context  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        if self.context.debug:
            super().log_message(format, *args)

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def _dispatch(self) -> None:
        parsed = parse.urlsplit(self.path)
        params = parse.parse_qs(parsed.query, keep_blank_values=True)
        body = self._read_body()

        try:
            if parsed.path == "/":
                status = self.context.device().status()
                self._json(200, {"ok": True, "service": "wrish-relay", "device": status})
                return
            if parsed.path == "/health":
                self._json(200, {"ok": True})
                return
            if parsed.path == "/battery":
                with self.context.ble_session(blocking=True, reason="relay-battery"):
                    battery = self.context.device().read_battery()
                self._json(200, {"battery": battery})
                return
            if parsed.path == "/find":
                with self.context.ble_session(blocking=True, reason="relay-find"):
                    self.context.device().find_device()
                self._json(200, {"ok": True, "action": "find"})
                return
            if parsed.path == "/vibrate":
                with self.context.ble_session(blocking=True, reason="relay-vibrate"):
                    self.context.device().vibrate()
                self._json(200, {"ok": True, "action": "vibrate"})
                return
            if parsed.path == "/sms":
                sender = self._first(params, "from", "sender")
                if not sender:
                    self._json(400, {"error": "missing 'from' query parameter"})
                    return
                with self.context.ble_session(blocking=True, reason="relay-sms"):
                    self.context.device().send_sms(sender=sender, text=body, do_init=True)
                self._json(200, {"ok": True, "action": "sms", "from": sender})
                return
            if parsed.path == "/call":
                caller = self._first(params, "from", "caller")
                number = self._first(params, "number")
                with self.context.ble_session(blocking=True, reason="relay-call"):
                    self.context.device().send_call(caller=caller or "", number=number or "", do_init=True)
                self._json(200, {"ok": True, "action": "call", "from": caller, "number": number})
                return
            if parsed.path == "/notify":
                title = self._first(params, "title")
                if not title:
                    self._json(400, {"error": "missing 'title' query parameter"})
                    return
                app = self._first(params, "app") or "whatsapp"
                with self.context.ble_session(blocking=True, reason="relay-notify"):
                    self.context.device().send_notification(
                        app_name=app,
                        title=title,
                        body=body,
                        do_init=True,
                    )
                self._json(200, {"ok": True, "action": "notify", "app": app, "title": title})
                return

            self._json(404, {"error": "unknown endpoint"})
        except DeviceError as exc:
            self._json(502, {"error": str(exc)})
        except Exception as exc:
            self._json(500, {"error": str(exc)})

    def _first(self, params: dict[str, list[str]], *names: str) -> str | None:
        for name in names:
            values = params.get(name)
            if values:
                return values[0]
        return None

    def _read_body(self) -> str:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            return ""
        raw = self.rfile.read(content_length)
        return raw.decode("utf-8", errors="replace")

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8") + b"\n"
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class LocalCommandServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], context: RelayContext):
        super().__init__(address, LocalCommandHandler)
        self.context = context


class HookpoolRelay:
    def __init__(self, relay_url: str, local_base_url: str, *, debug: bool = False):
        self.relay_url = relay_url
        self.local_base_url = local_base_url.rstrip("/")
        self.debug = debug

    def _log(self, message: str) -> None:
        if self.debug:
            print(f"[relay] {message}")

    def run_forever(self) -> None:
        seq: int | None = None
        response_payload: dict[str, Any] | None = None

        while True:
            try:
                status, headers, body = self._poll(seq=seq, response_payload=response_payload)
                if status == 204:
                    seq = None
                    response_payload = None
                    continue
                if status != 200:
                    self._log(f"unexpected relay status {status}")
                    seq = None
                    response_payload = None
                    continue

                seq_header = headers.get("X-Relay-Seq") or headers.get("x-relay-seq")
                if not seq_header:
                    content_type = headers.get("Content-Type", "")
                    preview = body.decode("utf-8", errors="replace").strip().replace("\n", " ")
                    if len(preview) > 220:
                        preview = preview[:220] + "..."
                    self._log(
                        f"invalid relay response: missing X-Relay-Seq, "
                        f"content-type={content_type!r}, body={preview!r}"
                    )
                    seq = None
                    response_payload = None
                    time.sleep(3)
                    continue

                seq = int(seq_header)
                req_payload = json.loads(body.decode("utf-8"))
                response_payload = self._forward_local(req_payload)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                self._log(f"relay loop error: {exc}")
                seq = None
                response_payload = None
                time.sleep(3)

    def _poll(
        self,
        *,
        seq: int | None,
        response_payload: dict[str, Any] | None,
    ) -> tuple[int, dict[str, str], bytes]:
        data: bytes | None = None
        headers = {"Content-Type": "application/json"}

        if seq is not None and response_payload is not None:
            headers["X-Relay-Seq"] = str(seq)
            data = json.dumps(response_payload).encode("utf-8")

        req = request.Request(
            self.relay_url,
            data=data,
            headers=headers,
            method="PATCH",
        )

        with request.urlopen(req, timeout=35) as response:
            return response.status, dict(response.headers.items()), response.read()

    def _forward_local(self, req_payload: dict[str, Any]) -> dict[str, Any]:
        path = req_payload.get("path", "/")
        query_string = req_payload.get("query_string", "")
        full_url = f"{self.local_base_url}{path}"
        if query_string:
            full_url = f"{full_url}?{query_string}"

        body = _decode_payload(req_payload.get("body", ""), bool(req_payload.get("body_base64")))
        method = str(req_payload.get("method", "GET")).upper()
        headers = _filtered_headers(dict(req_payload.get("headers", {})))

        local_request = request.Request(full_url, data=body or None, headers=headers, method=method)

        try:
            with request.urlopen(local_request, timeout=35) as response:
                content_type = response.headers.get("Content-Type", "")
                response_body = _ensure_trailing_newline(response.read(), content_type)
                payload, body_base64 = _coerce_text(response_body)
                return {
                    "status": response.status,
                    "headers": _filtered_headers(dict(response.headers.items())),
                    "body": payload,
                    "body_base64": body_base64,
                }
        except error.HTTPError as exc:
            content_type = exc.headers.get("Content-Type", "")
            response_body = _ensure_trailing_newline(exc.read(), content_type)
            payload, body_base64 = _coerce_text(response_body)
            return {
                "status": exc.code,
                "headers": _filtered_headers(dict(exc.headers.items())),
                "body": payload,
                "body_base64": body_base64,
            }
        except Exception as exc:
            payload = json.dumps({"error": str(exc)})
            return {
                "status": 502,
                "headers": {"Content-Type": "application/json"},
                "body": payload,
                "body_base64": False,
            }


def pick_free_port(bind: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((bind, 0))
        return int(sock.getsockname()[1])


def run_relay(
    *,
    relay_url: str,
    mac: str,
    hci: str,
    bind: str,
    port: int,
    debug: bool,
    sentinel: bool,
    sentinel_interval: float,
    sentinel_app: str,
    sentinel_title: str,
    sentinel_body: str,
) -> None:
    if not relay_url.endswith(".relay"):
        raise DeviceError("Relay URL must point to a .relay endpoint")

    actual_port = port if port != 0 else pick_free_port(bind)
    public_base_url = relay_url[:-6]
    context = RelayContext(mac=mac, hci=hci, debug=debug, lock=threading.Lock())
    server = LocalCommandServer((bind, actual_port), context)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    sentinel_thread: threading.Thread | None = None
    if sentinel:
        sentinel_thread = threading.Thread(
            target=_run_sentinel_loop,
            kwargs={
                "mac": mac,
                "hci": hci,
                "debug": debug,
                "interval": sentinel_interval,
                "app": sentinel_app,
                "title": sentinel_title,
                "body": sentinel_body,
            },
            daemon=True,
        )
        sentinel_thread.start()

    local_base_url = f"http://{bind}:{actual_port}"
    print(f"Local relay server: {local_base_url}")
    print(f"Public webhook URL: {public_base_url}")
    print("Forwarded endpoints: /, /health, /battery, /find, /vibrate, /sms, /call, /notify")
    if sentinel:
        print("Sentinel: enabled")
    print("Examples:")
    print(f"  curl '{public_base_url}/'")
    print(f"  curl '{public_base_url}/health'")
    print(f"  curl '{public_base_url}/battery'")
    print(f"  curl -X POST '{public_base_url}/find'")
    print(f"  curl -X POST '{public_base_url}/sms?from=asasasd' -d 'ciao'")
    print(f"  curl -X POST '{public_base_url}/call?from=Mario&number=+39123456789'")
    print(f"  curl -X POST '{public_base_url}/notify?app=whatsapp&title=Ciao' -d 'messaggio'")

    relay = HookpoolRelay(relay_url=relay_url, local_base_url=local_base_url, debug=debug)
    try:
        relay.run_forever()
    finally:
        server.shutdown()
        server.server_close()

def _run_sentinel_loop(
    *,
    mac: str,
    hci: str,
    debug: bool,
    interval: float,
    app: str,
    title: str,
    body: str,
) -> None:
    device = C60A82CDevice(mac=mac, hci=hci, debug=debug)
    announced = False
    last_diagnosis = 0.0
    recovery_failures = 0

    while True:
        now = time.monotonic()

        try:
            with ble_session(blocking=False, reason="relay-sentinel"):
                # Proactive adapter self-diagnosis every DIAGNOSIS_INTERVAL seconds
                if now - last_diagnosis >= _SENTINEL_DIAGNOSIS_INTERVAL:
                    result = device.diagnose_adapter()
                    last_diagnosis = now
                    if debug:
                        if result["powered"]:
                            print("[sentinel] periodic diagnosis ok")
                        elif result["error"]:
                            print(f"[sentinel] periodic diagnosis failed: {result['error']}")

                connected = device.is_connected()
                if not connected:
                    announced = False
                    if debug:
                        print("[sentinel] recovery attempt started")
                    device.connect()
                    connected = True
                    recovery_failures = 0

                if connected and not announced:
                    if debug:
                        print("[sentinel] connected, sending notification...")
                    device.send_notification(app_name=app, title=title, body=body, do_init=True)
                    if debug:
                        print("[sentinel] notification sent")
                    announced = True
                    recovery_failures = 0
                elif connected:
                    recovery_failures = 0
        except BleLockBusyError:
            if debug:
                print("[sentinel] paused, BLE busy with another command")
        except Exception as exc:
            announced = False
            recovery_failures += 1
            if debug:
                print(f"[sentinel] {exc}")
            maybe_cycle_sentinel_adapter(
                device,
                recovery_failures,
                log_fn=print if debug else None,
            )

        time.sleep(max(interval, 0.2))
