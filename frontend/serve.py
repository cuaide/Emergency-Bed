from __future__ import annotations

import argparse
import urllib.error
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parent

PROXY_PREFIXES = (
    "/api/",
    "/health",
    "/status",
    "/config/",
    "/places",
    "/docs",
    "/openapi.json",
)

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "content-encoding",
    "content-length",
}


class ProxyingHandler(SimpleHTTPRequestHandler):
    backend = "http://localhost:8000"

    def _should_proxy(self) -> bool:
        return self.path.startswith(PROXY_PREFIXES)

    def _proxy(self, body: bytes | None = None) -> None:
        target = f"{self.backend.rstrip('/')}{self.path}"
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP and key.lower() != "host"
        }
        request = urllib.request.Request(
            target, data=body, headers=headers, method=self.command
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read()
                status = response.status
                out_headers = response.headers
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            status = exc.code
            out_headers = exc.headers
        except urllib.error.URLError as exc:
            self.send_error(502, f"백엔드에 연결할 수 없습니다 ({self.backend}): {exc.reason}")
            return

        self.send_response(status)
        for key, value in out_headers.items():
            if key.lower() not in HOP_BY_HOP:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_body(self) -> bytes | None:
        length = self.headers.get("Content-Length")
        return self.rfile.read(int(length)) if length else None

    def do_GET(self) -> None: 
        if self._should_proxy():
            self._proxy()
            return
        super().do_GET()

    def do_HEAD(self) -> None:  
        if self._should_proxy():
            self._proxy()
            return
        super().do_HEAD()

    def do_POST(self) -> None: 
        if self._should_proxy():
            self._proxy(self._read_body())
            return
        self.send_error(405, "정적 파일에는 POST 할 수 없습니다.")

    def end_headers(self) -> None:
        if not self._should_proxy():
            self.send_header("Cache-Control", "no-store")
        super().end_headers()


def main() -> None:
    parser = argparse.ArgumentParser(description="프론트엔드 개발 서버 (백엔드 프록시 포함)")
    parser.add_argument("--port", type=int, default=5173, help="정적 서버 포트 (기본 5173)")
    parser.add_argument(
        "--backend",
        default="http://localhost:8000",
        help="프록시할 FastAPI 주소 (기본 http://localhost:8000)",
    )
    args = parser.parse_args()

    ProxyingHandler.backend = args.backend
    handler = partial(ProxyingHandler, directory=str(FRONTEND_DIR))

    with ThreadingHTTPServer(("0.0.0.0", args.port), handler) as httpd:
        print(f"프론트엔드  http://localhost:{args.port}")
        print(f"API 프록시  {'/api/*, /health, /status, /config/*, /places'} → {args.backend}")
        print("종료: Ctrl+C")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n종료합니다.")

if __name__ == "__main__":
    main()
