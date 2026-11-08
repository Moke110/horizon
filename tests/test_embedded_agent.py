"""Exercise the real Pi SDK against a local OpenAI-compatible server."""
import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from hostrun import embedded_agent

ROOT = Path(__file__).resolve().parents[1]


def test_distribution_keeps_pi_catalog_but_excludes_private_dotfiles(tmp_path):
    from hostrun import distribution
    source = tmp_path / "source"
    asset = "agent/node_modules/@earendil-works/pi-ai/dist/providers/data/.manifest.json"
    target = source / asset
    target.parent.mkdir(parents=True)
    target.write_text('{}')
    (source / "agent/.env").write_text("SECRET=private")
    built = distribution.build(source=source, out=tmp_path / "dist", version="0.1.0", channel="prerelease",
                               compat={"os": "windows", "arch": "x86_64"})
    files = json.loads(Path(built["manifest"]).read_text())["files"]
    assert asset in {entry["path"] for entry in files}
    assert "agent/.env" not in {entry["path"] for entry in files}


def test_model_config_is_not_in_public_facts_or_argv(tmp_path):
    embedded_agent.configure(tmp_path, provider="deepseek", model="deepseek-chat", api_key="private-test-key")
    facts = embedded_agent.runtime(tmp_path, code=ROOT)
    assert facts["configured"]
    assert "private-test-key" not in json.dumps(facts)
    if facts["ok"]:
        assert "private-test-key" not in " ".join(embedded_agent.argv(tmp_path, "hello", kind="agent", code=ROOT))


@pytest.mark.skipif(not shutil.which("node") or not (ROOT / "agent/dist/main.js").exists(), reason="build the Pi agent first")
@pytest.mark.parametrize("kind", ["intent_judgement", "kb_read", "push", "push_tool", "push_private", "push_database", "push_snapshot", "push_hardlink"])
def test_real_pi_stream_and_tool_policy(tmp_path, kind):
    requests = []
    database = tmp_path / "horizon.sqlite3"
    database.write_text("preserved database")
    if kind == "push_hardlink":
        (tmp_path / "alias.json").hardlink_to(database)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunks = [({"role": "assistant", "content": "Horizon ready"}, None), ({}, "stop")]
            if kind in {"push_tool", "push_private", "push_database", "push_snapshot", "push_hardlink"} and len(requests) == 1:
                target = tmp_path / ("agent/model.json" if kind == "push_private" else "outbox/result.json")
                if kind == "push_database": target = database
                if kind == "push_snapshot": target = tmp_path / "snapshots/state.json"
                if kind == "push_hardlink": target = tmp_path / "alias.json"
                chunks = [({"role": "assistant", "tool_calls": [{"index": 0, "id": "call_test", "type": "function",
                    "function": {"name": "write_file", "arguments": json.dumps({"path": str(target), "content": '{"ok":true}'})}}]}, None), ({}, "tool_calls")]
            for delta, finish in chunks:
                event = {"id": "test", "object": "chat.completion.chunk", "created": 1, "model": "test-model",
                         "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        embedded_agent.configure(tmp_path, provider="horizon-test", model="test-model", api_key="private-test-key",
                                 base_url=f"http://127.0.0.1:{server.server_port}/v1")
        import os, sys
        proc = subprocess.run(embedded_agent.argv(tmp_path, "Say ready", kind=kind, code=ROOT), cwd=ROOT,
                              env={**os.environ, "HORIZON_PYTHON": sys.executable}, capture_output=True,
                              text=True, encoding="utf-8", timeout=45)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        events = [json.loads(line) for line in proc.stdout.splitlines()]
        assert next(e for e in events if e["type"] == "final")["text"] == "Horizon ready"
        assert "private-test-key" not in proc.stdout + proc.stderr
        names = {t["function"]["name"] for t in requests[0].get("tools", [])}
        expected = set() if kind == "intent_judgement" else {"read_file"} if kind == "kb_read" else {"horizon_cli", "read_file", "write_file"}
        assert names == expected
        assert "Development responsibilities" not in json.dumps(requests)
        if kind == "push_tool":
            assert json.loads((tmp_path / "outbox/result.json").read_text()) == {"ok": True}
            assert len(requests) == 2
            assert any(e["type"] == "tool_result" and not e["isError"] for e in events)
        if kind in {"push_database", "push_snapshot", "push_hardlink"}:
            assert database.read_text() == "preserved database"
            assert not (tmp_path / "snapshots/state.json").exists()
            assert any(e["type"] == "tool_result" and e["isError"] for e in events)
        if kind == "push_private":
            assert json.loads(embedded_agent.config_path(tmp_path).read_text())["apiKey"] == "private-test-key"
            assert any(e["type"] == "tool_result" and e["isError"] for e in events)
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
