import json
from pathlib import Path
import queue
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from hostrun import embedded_agent
from hostrun.agent_scheduling import command
from hostrun.agent_service import AgentService
from hostrun.desktop_worker import DesktopWorker
from hostrun.schedule_runtime import ScheduleRuntime


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not shutil.which("node") or not (ROOT / "agent/dist/main.js").exists(), reason="build Pi first")
@pytest.mark.parametrize("entry,choice", [
    ("manual", "default"), ("schedule", "default"),
    ("manual", "explicit"), ("schedule", "explicit"), ("manual", "empty"),
    ("manual", "recover"),
])
def test_retriever_search_uses_selected_sources_and_keeps_provider_failures_local(tmp_path, monkeypatch, entry, choice):
    from retriever import registry

    catalog = [
        {"id": "alpha", "name": "Alpha", "enabled": choice != "empty"},
        {"id": "beta", "name": "Beta", "enabled": choice != "empty"},
        {"id": "gamma", "name": "Gamma", "enabled": False},
    ]
    monkeypatch.setattr(registry, "list_sources", lambda home: catalog)
    source_calls = []

    def search_source(home, source, query):
        source_calls.append((source, query))
        if source == "alpha":
            return {"error_code": "unconfigured", "message": "Alpha 缺少凭据"}
        return {"source": source, "results": [{"title": "graph spectra", "url": "https://example.com/result"}]}

    monkeypatch.setattr(registry, "search_source", search_source)
    exchanges = []
    if choice == "default":
        steps = [
            ("retriever_select_sources", {"sources": []}),
            ("alpha_search", {"query": "graphs"}),
            ("beta_search", {"query": "graphs"}),
            ("beta_search", {"query": "graph spectra"}),
        ]
        expected = [("alpha", "graphs"), ("beta", "graphs"), ("beta", "graph spectra")]
        report = "Alpha 缺少凭据，Beta 返回 graph spectra。"
    elif choice == "explicit":
        steps = [
            ("retriever_select_sources", {"sources": ["gamma"]}),
            ("beta_search", {"query": "excluded"}),
            ("gamma_search", {"query": "graphs"}),
            ("gamma_search", {"query": "graph spectra"}),
        ]
        expected = [("gamma", "graphs"), ("gamma", "graph spectra")]
        report = "Gamma 返回 graph spectra。"
    elif choice == "recover":
        steps = [
            ("retriever_select_sources", {"sources": ["beta"]}),
            ("beta_search", {"query": []}),
            None,
            ("beta_search", {"query": "graph spectra"}),
        ]
        expected = [("beta", "graph spectra")]
        report = "Recovered retrieval."
    else:
        steps = [("retriever_select_sources", {"sources": []})]
        expected = []
        report = "请在检索器中启用信息源。"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            step = len(exchanges)
            exchanges.append(request)
            if step < len(steps):
                if steps[step] is None:
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error":{"message":"invalid key","type":"authentication"}}')
                    return
                name, arguments = steps[step]
                delta = {"role": "assistant", "tool_calls": [{
                    "index": 0, "id": f"call-{step}", "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }]}
                finish = "tool_calls"
            else:
                content = report if entry == "manual" else json.dumps({
                    "schema_version": 1, "result": report, "coverage": {
                        "from_at": 100, "until_at": 160, "covered_until": 160, "status": "complete",
                    },
                })
                delta = {"role": "assistant", "content": content}
                finish = "stop"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for data, reason in [(delta, None), ({}, finish)]:
                chunk = {
                    "id": "test", "object": "chat.completion.chunk", "created": 1,
                    "model": "test-model", "choices": [{"index": 0, "delta": data, "finish_reason": reason}],
                }
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    service = AgentService(tmp_path, token="s")
    embedded_agent.configure(tmp_path, provider="horizon-test", model="test-model",
                             api_key="local-only", base_url=f"http://127.0.0.1:{server.server_port}/v1")
    resolved = {"code": str(ROOT)}
    runtime = ScheduleRuntime(service, "s", resolved, lambda: {})
    scope = "Only use Gamma to search graphs." if choice == "explicit" else "Search graphs."
    try:
        if entry == "schedule":
            command(service, {
                "schema_version": 1, "operation_id": "schedule", "title": "Search",
                "scope": scope, "interval_seconds": 60, "start_at": 100, "next_at": 160,
            }, token="s")
            runtime.step(now=160)
            deadline = time.monotonic() + 20
            while runtime.workers and time.monotonic() < deadline:
                time.sleep(.05)
                runtime.step(now=160)
            assert not runtime.workers
        else:
            task = service.create_task({"schema_version": 1, "operation_id": "task", "title": "Search"}, token="s")
            turn = service.begin_turn({
                "schema_version": 1, "operation_id": "turn", "task_id": task["task_id"], "expected_version": 1,
                "text": scope,
            }, token="s")
            worker = DesktopWorker()
            worker.home, worker.resolved = tmp_path, resolved
            worker.events, worker.answers = queue.Queue(), queue.Queue()
            worker.closed, worker.cancelled, worker.process = False, threading.Event(), None
            worker.service, worker.service_token = service, "s"
            worker.task_id, worker.input_version = task["task_id"], turn["input_version"]
            worker.turn, worker.version_provider = turn, lambda: {}
            worker.run_agent(scope, turn["session_id"])
            if choice == "recover":
                failed, = service.list_turns(token="s")
                assert failed["status"] == "recovery_required"
                assert failed["failure"]["category"] == "authentication"
                worker.turn = service.transition_turn({
                    "schema_version": 1, "operation_id": "resume", "turn_id": failed["turn_id"],
                    "expected_version": failed["version"], "action": "resume",
                }, token="s")
                worker.run_agent(scope, turn["session_id"])
        assert source_calls == expected
        assert len(exchanges) == len(steps) + 1
        assert {tool["function"]["name"] for tool in exchanges[0]["tools"]} >= {
            "retriever_select_sources", "alpha_search", "beta_search", "gamma_search",
        }
        last_tool_results = [message["content"] for message in exchanges[-1]["messages"] if message["role"] == "tool"]
        if choice == "default":
            assert any("unconfigured" in data for data in last_tool_results)
            assert "graph spectra" in last_tool_results[-1]
        elif choice == "explicit":
            assert any("source_not_selected" in data for data in last_tool_results)
        elif choice == "recover":
            assert "graph spectra" in last_tool_results[-1]
            assert any("Validation failed" in data for data in last_tool_results)
        else:
            assert "no_enabled_sources" in last_tool_results[-1]
        turn, = service.list_turns(token="s")
        assert turn["status"] == "completed"
        assert (turn["result"] if entry == "manual" else json.loads(turn["result"])["result"]) == report
        assert [item["enabled"] for item in registry.list_sources(tmp_path)] == [choice != "empty", choice != "empty", False]
    finally:
        runtime.stop()
        server.shutdown()
        server.server_close()
        thread.join()
