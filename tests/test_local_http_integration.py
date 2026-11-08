"""Actual localhost HTTP round-trip tests for the local model adapter.

A small in-process deterministic fixture simulates only the HTTP protocol.
It is NOT a real llama.cpp or language-model quality acceptance run.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import unittest

from forgeos.local_models import LocalChatModel


class RealLoopbackProtocolTests(unittest.TestCase):
    def test_auth_and_plan_edit_round_trip_without_proxy_or_cloud(self):
        observed = []
        token = "b" * 32

        class Fixture(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, body):
                value = json.dumps(body).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(value)))
                self.end_headers()
                self.wfile.write(value)

            def do_GET(self):
                observed.append((self.command, self.path,
                                 self.headers.get("Authorization")))
                if self.path != "/v1/models":
                    self.send_error(404)
                    return
                if self.headers.get("Authorization") != "Bearer " + token:
                    self.send_error(401)
                    return
                self.respond({"data": [{
                    "id": "forgeos-managed",
                    "size": 1000,
                }]})

            def do_POST(self):
                if self.path != "/v1/chat/completions":
                    self.send_error(404)
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if length > 4096 or length == 0:
                    self.send_error(413)
                    return
                body = json.loads(self.rfile.read(length))
                observed.append((self.command, self.path,
                                 self.headers.get("Authorization"), body))
                if self.headers.get("Authorization") != "Bearer " + token:
                    self.send_error(401)
                    return
                self.respond({
                    "choices": [{
                        "finish_reason": "stop",
                        "message": {"role": "assistant",
                                    "content": '{"files":[]}'},
                    }],
                })

        server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        daemon = threading.Thread(target=server.serve_forever, daemon=True)
        daemon.start()
        try:
            host, port = server.server_address
            self.assertEqual(host, "127.0.0.1")
            model = LocalChatModel(
                backend="llama.cpp", model="forgeos-managed",
                endpoint=f"http://127.0.0.1:{port}/v1",
                api_key=token)
            self.assertEqual(model.installed()[0]["name"], "forgeos-managed")
            self.assertEqual(model.plan("Explain this program"),
                             '{"files":[]}')
            self.assertEqual(model.generate_edits("Fix this program"),
                             '{"files":[]}')
            self.assertEqual(len(observed), 3)
            self.assertEqual([v[0] for v in observed], ["GET", "POST", "POST"])
            self.assertTrue(all(v[2] == "Bearer " + token for v in observed))
            self.assertEqual(observed[-1][3]["model"], "forgeos-managed")
            self.assertEqual(observed[-1][3]["stream"], False)
            self.assertEqual(observed[-1][3]["temperature"], 0)
        finally:
            server.shutdown()
            server.server_close()
            daemon.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
