"""End-to-end through the WSGI app (the exact object Vercel runs):
web request -> API -> routing system -> assignment engine / database -> notifier."""
import io
import json
import unittest
from urllib.parse import urlencode

from nursing_dss.web import create_app
from tests.base import SystemTestCase


class ApiTestCase(SystemTestCase):
    def setUp(self):
        super().setUp()
        self.app = create_app(self.system)

    def call(self, method, path, body=None, query=None):
        raw = json.dumps(body).encode() if body is not None else b""
        env = {"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": urlencode(query or {}),
               "CONTENT_LENGTH": str(len(raw)), "wsgi.input": io.BytesIO(raw)}
        captured = {}

        def start_response(status, headers):
            captured["status"] = int(status.split()[0])
        data = b"".join(self.app(env, start_response))
        return captured["status"], json.loads(data)

    def test_full_workflow_through_the_api(self):
        code, snap = self.call("GET", "/api/snapshot")
        self.assertEqual(code, 200)
        uid = snap["unit"]["unit_id"]
        self.assertEqual((len(snap["rooms"]), len(snap["nurses"])), (6, 6))

        code, rec = self.call("POST", "/api/assignments/recommend", {"unit_id": uid})
        self.assertEqual(code, 200)
        self.assertEqual(rec["violations"], [])
        self.assertEqual(len(rec["plan"]), 12)

        # charge nurse edits the draft so it breaks a rule -> commit is refused
        bed = next(iter(rec["plan"]))
        rpn = next(n["nurse_id"] for n in snap["nurses"] if n["qualification"] == "RPN")
        bad = dict(rec["plan"])
        acuity5 = [b["bed_id"] for r in snap["rooms"] for b in r["beds"] if b["acuity"] == 5][0]
        bad[str(acuity5)] = rpn
        code, ev = self.call("POST", "/api/assignments/evaluate", {"unit_id": uid, "plan": bad})
        self.assertIn("QUALIFICATION", [v["code"] for v in ev["violations"]])
        code, refused = self.call("POST", "/api/assignments/commit", {"unit_id": uid, "plan": bad})
        self.assertFalse(refused["saved"])
        code, forced = self.call("POST", "/api/assignments/commit",
                                 {"unit_id": uid, "plan": bad, "override": True})
        self.assertTrue(forced["saved"])

        # save the clean recommendation, then a bell press follows it
        self.call("POST", "/api/assignments/commit", {"unit_id": uid, "plan": rec["plan"]})
        bell = next(b["bell_id"] for r in snap["rooms"] for b in r["beds"] if str(b["bed_id"]) == bed)
        code, routed = self.call("POST", "/api/bell", {"bell_id": bell})
        self.assertTrue(routed["ok"])
        self.assertEqual(routed["nurse_id"], rec["plan"][bed])

        code, alerts = self.call("GET", "/api/alerts")
        self.assertEqual(alerts["alerts"][0]["alert_id"], routed["alert_id"])
        self.assertEqual(len(alerts["inbox"]), 1)
        code, ack = self.call("POST", "/api/alerts/ack", {"alert_id": routed["alert_id"]})
        self.assertIn("response_seconds", ack)

    def test_escalation_endpoint(self):
        self.call("POST", "/api/assignments/manual",
                  {"nurse_id": self.nurse_id("Bob Jones"), "level": "Room", "target_id": self.room_id("101")})
        code, r = self.call("POST", "/api/bell", {"bell_id": 1011})
        code, esc = self.call("POST", "/api/alerts/escalate", {"elapsed_seconds": 61})
        self.assertEqual(len(esc["results"]), 1)
        self.assertTrue(esc["results"][0]["ok"])

    def test_errors_are_clean_json(self):
        self.assertEqual(self.call("POST", "/api/bell", {"bell_id": "abc"})[0], 400)
        self.assertEqual(self.call("POST", "/api/bell", {})[0], 400)
        self.assertEqual(self.call("POST", "/api/nope", {})[0], 404)
        self.assertEqual(self.call("POST", "/api/setup/patient", {"bed_id": 1, "acuity": 9})[0], 400)
        code, body = self.call("POST", "/api/setup/room", {"room_number": "9"})
        self.assertEqual(code, 400)
        self.assertIn("Missing field", body["error"])
        self.assertEqual(self.call("POST", "/api/alerts/ack", {"alert_id": 999})[0], 400)

    def test_setup_endpoints_build_a_unit_that_routes(self):
        _, u = self.call("POST", "/api/setup/unit", {"name": "Ward B"})
        _, r = self.call("POST", "/api/setup/room", {"room_number": "1", "unit_id": u["unit_id"], "x": 5, "y": 5})
        _, b = self.call("POST", "/api/setup/bed", {"bed_number": "A", "room_id": r["room_id"]})
        self.call("POST", "/api/setup/bell", {"bell_id": 7001, "bed_id": b["bed_id"]})
        self.call("POST", "/api/setup/patient", {"bed_id": b["bed_id"], "acuity": 3})
        _, n = self.call("POST", "/api/setup/nurse", {"name": "Zed", "contact": "EXT-9", "qualification": "RN",
                                                      "max_workload": 10})
        self.call("POST", "/api/assignments/manual", {"nurse_id": n["nurse_id"], "level": "Room",
                                                      "target_id": r["room_id"]})
        _, routed = self.call("POST", "/api/bell", {"bell_id": 7001})
        self.assertEqual(routed["nurse_name"], "Zed")


class HttpGateway(SystemTestCase):
    def test_gateway_routes_a_posted_signal(self):
        import threading
        import urllib.error
        import urllib.request
        from nursing_dss.input.http_gateway import make_server
        self.db.assign_room(self.nurse_id("Bob Jones"), self.room_id("101"))
        server = make_server(self.system, "127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/bell"
            req = urllib.request.Request(url, json.dumps({"bell_id": 1011}).encode(), method="POST")
            body = json.loads(urllib.request.urlopen(req).read())
            self.assertTrue(body["ok"])
            self.assertEqual(body["nurse_name"], "Bob Jones")
            bad = urllib.request.Request(url, b'{"bell_id": "x"}', method="POST")
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(bad)
            self.assertEqual(ctx.exception.code, 400)
        finally:
            server.shutdown()


if __name__ == "__main__":
    unittest.main()
