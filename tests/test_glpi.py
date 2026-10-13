"""Pruebas de la sincronización forzada con GLPI usando un «agente» falso (servidor HTTP local)."""
import os
import queue
import socket
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from girag_maint.core.context import TaskContext
from girag_maint.tasks import glpi_sync as g


def make_ctx(dry=False):
    return TaskContext(queue.Queue(), threading.Event(), dry_run=dry)


class FakeAgent:
    """Imita la página local del GLPI Agent: /now dispara; /status: running unas veces y luego waiting."""

    def __init__(self, mode="ok", log_path=None):
        self.mode, self.log_path, self.triggered, self.polls_after = mode, log_path, False, 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body):
                self.send_response(code)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(body.encode())

            def do_GET(self):
                if self.path == "/now":
                    if outer.mode == "untrusted":
                        return self._send(403, "untrusted address")
                    outer.triggered = True
                    if outer.log_path:
                        with open(outer.log_path, "a") as f:
                            f.write("[info] running task Inventory\n[info] sending inventory\n[info] inventory accepted\n")
                    return self._send(200, "OK")
                if self.path == "/status":
                    if outer.triggered and outer.mode == "ok":
                        outer.polls_after += 1
                        return self._send(200, "status: running task Inventory" if outer.polls_after <= 3 else "status: waiting")
                    return self._send(200, "status: waiting")
                self._send(404, "no")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class HelpersTests(unittest.TestCase):
    def test_same_server(self):
        u = "https://soporte.girag.com/plugins/glpiinventory"
        self.assertTrue(g.same_server(u + "/", u))
        self.assertTrue(g.same_server("https://otro/x, " + u.upper(), u))
        self.assertFalse(g.same_server("https://otro/plugins/glpiinventory", u))

    def test_parse_status(self):
        self.assertEqual(g.parse_status("status: running task Inventory"), "running")
        self.assertEqual(g.parse_status("<p>status: waiting</p>"), "waiting")
        self.assertIsNone(g.parse_status("hola"))

    def test_logtail_only_new_lines_and_rotation(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.log"
            p.write_text("vieja\n")
            t = g.LogTail([str(p)])
            self.assertEqual(t.read_new(), [])
            p.write_text("vieja\nnueva 1\nnueva 2\n")
            self.assertEqual(t.read_new(), ["nueva 1", "nueva 2"])
            p.write_text("rotado\n")                   # más corto que antes: se reinicia
            self.assertEqual(t.read_new(), ["rotado"])


class ServiceMethodTests(unittest.TestCase):
    def test_trigger_ok_untrusted_and_unreachable(self):
        a = FakeAgent()
        try:
            self.assertTrue(g.trigger_now(a.port)[0])
        finally:
            a.close()
        b = FakeAgent("untrusted")
        try:
            ok, why = g.trigger_now(b.port)
            self.assertFalse(ok)
            self.assertIn("httpd-trust", why)
        finally:
            b.close()
        ok, why = g.trigger_now(closed_port())
        self.assertFalse(ok)
        self.assertIn("no responde", why)

    def test_sync_via_service_follows_running_to_waiting(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "glpi-agent.log"
            log.write_text("antes\n")
            a = FakeAgent("ok", str(log))
            try:
                ctx = make_ctx()
                cfg = dict(g.DEFAULT_CFG)
                ok, why, lines = g.sync_via_service(ctx, cfg, a.port, g.LogTail([str(log)]), poll=0.02, grace=3)
            finally:
                a.close()
            self.assertTrue(ok, why)
            self.assertIn("[info] inventory accepted", lines)
            prog = []
            while not ctx.events.empty():
                k, *p = ctx.events.get()
                if k == "progress":
                    prog.append(p[0])
            self.assertTrue(prog and max(prog) < 100)     # el 100 % lo marca la tarea al terminar

    def test_sync_via_service_detects_agent_that_never_runs(self):
        a = FakeAgent("silent")
        try:
            ok, why, _ = g.sync_via_service(make_ctx(), dict(g.DEFAULT_CFG), a.port, g.LogTail([]), poll=0.02, grace=0.3)
        finally:
            a.close()
        self.assertFalse(ok)
        self.assertIn("no se observó", why)


class TaskFlowTests(unittest.TestCase):
    def setUp(self):
        self.orig = (g.find_agent, g.service_state, g.read_agent_settings, g.check_server, g.load_config,
                     g.start_service)
        g.check_server = lambda url, timeout=12: (True, "HTTP 200")
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "agent.log"
        self.log.write_text("")
        cfg = dict(g.DEFAULT_CFG, log_paths=[str(self.log)], poll_seconds=0.02, start_grace_seconds=1.0)
        g.load_config = lambda: cfg
        self.fake_cli = Path(self.tmp.name) / "agent.sh"
        self.fake_cli.write_text("#!/bin/sh\necho '[info] target server0'\necho '[info] running task Inventory'\n"
                                 "echo '[info] sending inventory'\necho '[info] inventory accepted'\n")
        self.fake_cli.chmod(0o755)
        g.find_agent = lambda cfg: str(self.fake_cli)

    def tearDown(self):
        (g.find_agent, g.service_state, g.read_agent_settings, g.check_server, g.load_config,
         g.start_service) = self.orig
        self.tmp.cleanup()

    @unittest.skipIf(os.name == "nt", "usa scripts sh")
    def test_forces_via_installed_agent_service(self):
        a = FakeAgent("ok", str(self.log))
        try:
            g.service_state = lambda n: "RUNNING"
            g.read_agent_settings = lambda: {"server": g.DEFAULT_CFG["server_url"], "httpd-port": a.port}
            res = g.GlpiSyncTask().run(make_ctx())
        finally:
            a.close()
        self.assertTrue(res.ok, res)
        self.assertIn("agente instalado", res.summary)
        self.assertTrue(a.triggered)

    @unittest.skipIf(os.name == "nt", "usa scripts sh")
    def test_falls_back_to_cli_when_service_rejects(self):
        a = FakeAgent("untrusted")
        try:
            g.service_state = lambda n: "RUNNING"
            g.read_agent_settings = lambda: {"httpd-port": a.port}
            res = g.GlpiSyncTask().run(make_ctx())
        finally:
            a.close()
        self.assertTrue(res.ok, res)
        self.assertIn("línea de comandos", res.summary)

    @unittest.skipIf(os.name == "nt", "usa scripts sh")
    def test_other_configured_server_uses_cli_with_girag_url(self):
        g.service_state = lambda n: "RUNNING"
        g.read_agent_settings = lambda: {"server": "https://otro.servidor/plugins/glpiinventory"}
        ctx = make_ctx()
        res = g.GlpiSyncTask().run(ctx)
        self.assertTrue(res.ok, res)
        self.assertIn("línea de comandos", res.summary)

    def test_stopped_service_that_cannot_start_falls_back(self):
        started = []
        g.service_state = lambda n: "STOPPED"
        g.start_service = lambda n, wait=20.0: started.append(n) or False
        g.read_agent_settings = lambda: {}
        res = g.GlpiSyncTask().run(make_ctx())
        self.assertEqual(started, ["glpi-agent"])
        self.assertTrue(res.ok or os.name == "nt", res)       # cae al CLI falso

    def test_dry_run_reports_plan_and_does_not_force(self):
        a = FakeAgent("ok", str(self.log))
        try:
            g.service_state = lambda n: "RUNNING"
            g.read_agent_settings = lambda: {"httpd-port": a.port}
            res = g.GlpiSyncTask().run(make_ctx(dry=True))
        finally:
            a.close()
        self.assertTrue(res.ok)
        self.assertFalse(a.triggered)
        self.assertIn("service", res.details)

    def test_nothing_installed(self):
        g.find_agent = lambda cfg: None
        g.service_state = lambda n: None
        res = g.GlpiSyncTask().run(make_ctx())
        self.assertFalse(res.ok)
        self.assertIn("No se encontró", res.summary)


if __name__ == "__main__":
    unittest.main()
