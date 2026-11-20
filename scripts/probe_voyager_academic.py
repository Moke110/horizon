"""Run with python -m scripts.probe_voyager_academic from the source checkout."""
import json
import platform
import subprocess
import tempfile

from hostrun.desktop_bridge import voyager_command
from hostrun.voyager_service import VoyagerService


def probe(source, home):
    token = "local-probe"
    service = VoyagerService(home, token=token)

    def dispatch(action, **params):
        return voyager_command(service, dict(action=action, **params), token=token)[1]

    start, end = 1577836800, 1609459200  # UTC calendar year 2020
    view = dispatch(
        "voyager_create", schema_version=1, operation_id="create",
        scope=dict(text="photometry", must_include=["photometr"], sources=[source]),
        window=dict(mode="incremental", basis="published", from_at=start,
                    until_at=end, overlap_seconds=0),
        limits=dict(candidates=1, queries=1, seconds=65),
    )
    for index in range(2):
        view = dispatch("voyager_execute", schema_version=1, operation_id=f"page-{index}",
                        request_id=view["request_id"], expected_version=view["version"],
                        **({"continue": True} if index else {}))
        attempt = view["queries"][0]["attempts"][-1]
        if attempt["status"] != "received":
            raise RuntimeError(str(view["gaps"]))
        rows = view["candidates"]
        if len(rows) != index + 1 or not all(start <= row["published_at"] < end for row in rows):
            raise RuntimeError("Expected a distinct in-window candidate on each page")
        if not attempt.get("source_query"):
            raise RuntimeError("Missing source query in desktop history")
    service = VoyagerService(home, token=token)
    if dispatch("voyager_open", request_id=view["request_id"]) != view:
        raise RuntimeError("Reopened desktop projection changed")
    return [row["source_id"] for row in view["candidates"]]


if __name__ == "__main__":
    result = dict(commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                  os=platform.platform(), python=platform.python_version(), sources={})
    for source in ("arxiv", "europepmc"):
        try:
            with tempfile.TemporaryDirectory() as home:
                ids = probe(source, home)
            result["sources"][source] = dict(status="passed", ids=ids)
        except Exception as error:
            result["sources"][source] = dict(status="failed", error=str(error))
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(any(item["status"] != "passed" for item in result["sources"].values()))
