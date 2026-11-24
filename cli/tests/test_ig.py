"""Run: python3 -m unittest discover cli/tests   (uses a local fake Graph API)"""
import io, json, os, subprocess, sys, tempfile, threading, unittest, urllib.parse
from contextlib import redirect_stdout, redirect_stderr
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
LOG = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def _handle(self):
        u = urllib.parse.urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode() if n else ""
        LOG.append((self.command, u.path, urllib.parse.parse_qs(u.query), body, self.headers.get("Authorization")))
        if self.headers.get("Authorization") != "Bearer good":
            return self._send(401, {"error": {"code": 190, "message": "bad token"}})
        p = u.path
        if p.endswith("/me"): return self._send(200, {"user_id": "42", "username": "tester"})
        if p.endswith("/42/media") and self.command == "GET":
            return self._send(200, {"data": [{"id": "1", "media_product_type": "REELS"}, {"id": "2", "media_product_type": "FEED"}]})
        if p.endswith("/42/media"): return self._send(200, {"id": "c%d" % len(LOG)})
        if p.endswith("/42/media_publish"): return self._send(200, {"id": "published"})
        if "/c" in p: return self._send(200, {"status_code": "FINISHED"})
        return self._send(200, {"ok": True})
    do_GET = do_POST = do_DELETE = _handle


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        os.environ.update(IG_API_BASE=f"http://127.0.0.1:{cls.srv.server_port}",
                          IG_ACCESS_TOKEN="good", IG_USER_ID="42",
                          IG_CONFIG=os.path.join(tempfile.mkdtemp(), "c.json"))
        global ig
        import ig  # after env is set
        ig.BASE = os.environ["IG_API_BASE"]

    @classmethod
    def tearDownClass(cls): cls.srv.shutdown()

    def run_ig(self, *argv):
        LOG.clear(); o, e = io.StringIO(), io.StringIO()
        with redirect_stdout(o), redirect_stderr(e):
            rc = ig.main(list(argv))
        return rc, (json.loads(o.getvalue()) if o.getvalue() else None), e.getvalue()

    def test_me_and_auth_header(self):
        rc, out, _ = self.run_ig("me")
        self.assertEqual(rc, 0); self.assertEqual(out["username"], "tester")
        self.assertEqual(LOG[0][4], "Bearer good")

    def test_media_filter(self):
        _, out, _ = self.run_ig("media", "--type", "REELS")
        self.assertEqual([m["id"] for m in out], ["1"])

    def test_dry_run_sends_nothing(self):
        rc, out, _ = self.run_ig("post", "image", "https://x.io/a.jpg", "--caption", "hi")
        self.assertTrue(out["dry_run"]); self.assertEqual(LOG, [])
        rc, out, _ = self.run_ig("reply", "99", "thanks")
        self.assertTrue(out["dry_run"]); self.assertEqual(LOG, [])
        rc, out, _ = self.run_ig("delete-comment", "99")
        self.assertTrue(out["dry_run"]); self.assertEqual(LOG, [])

    def test_post_image_yes_flag_anywhere(self):
        rc, out, _ = self.run_ig("post", "image", "https://x.io/a.jpg", "--caption", "hi", "--yes")
        self.assertEqual(out, {"id": "published"})
        self.assertIn("image_url=https%3A%2F%2Fx.io%2Fa.jpg", LOG[0][3])

    def test_carousel(self):
        rc, out, _ = self.run_ig("--yes", "post", "carousel", "https://x.io/1.jpg", "https://x.io/2.mp4", "--caption", "c")
        self.assertEqual(out, {"id": "published"})
        bodies = [l[3] for l in LOG if l[0] == "POST" and l[1].endswith("/42/media")]
        self.assertEqual(len(bodies), 3)
        self.assertIn("VIDEO", bodies[1]); self.assertIn("CAROUSEL", bodies[2])

    def test_local_path_rejected(self):
        rc, _, err = self.run_ig("post", "reel", "./clip.mp4", "--yes")
        self.assertEqual(rc, 1); self.assertIn("public URL", err)

    def test_bad_token_message(self):
        os.environ["IG_ACCESS_TOKEN"] = "bad"
        try:
            rc, _, err = self.run_ig("me")
        finally:
            os.environ["IG_ACCESS_TOKEN"] = "good"
        self.assertEqual(rc, 1); self.assertIn("expired", err)

    def test_login_saves_private_config(self):
        rc, out, _ = self.run_ig("login", "--token", "good")
        self.assertEqual(out["username"], "tester")
        self.assertEqual(oct(ig.CONFIG_PATH.stat().st_mode & 0o777), "0o600")


if __name__ == "__main__":
    unittest.main()
