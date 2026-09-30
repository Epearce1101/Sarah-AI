# tests/durable_crash_worker.py
"""
Helper process for test_durable_tasks.py's crash tests.

    python durable_crash_worker.py <data_dir> start-sleep
    python durable_crash_worker.py <data_dir> start-approval
    python durable_crash_worker.py <data_dir> resume <workflow_id> [approve]
    python durable_crash_worker.py <data_dir> start-and-shutdown
    python durable_crash_worker.py <data_dir> status <workflow_id>

"start-*" starts a task, prints its workflow id, then idles until the test
kills this process (a simulated crash). "resume" starts Sarah's durable task
system again (which recovers interrupted tasks), optionally approves, waits
for the task to finish and prints the result as JSON.
"""
import json
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
pkg = types.ModuleType("backend")
pkg.__path__ = [str(ROOT)]
sys.modules["backend"] = pkg

from backend import db  # noqa: E402

data_dir = Path(sys.argv[1])
mode = sys.argv[2]
db.DATA_DIR = data_dir
db.DB_PATH = data_dir / "test.db"
db.init_db()

from backend import durable_tasks, procedure_memory  # noqa: E402
from dbos import DBOS  # noqa: E402

procedure_memory.init_tables()
durable_tasks.init_tables()


def fake_llm(prompt: str) -> str:
    # Every real LLM call leaves a mark, so the test can count them
    with open(data_dir / "llm_calls.txt", "a") as f:
        f.write("call\n")
    return "STEPS:\n1. Think\n2. Answer\nRESULT:\nAll done."


durable_tasks.configure(fake_llm, data_dir / "durable.sqlite")
durable_tasks.launch()

if mode == "start-sleep":
    started = durable_tasks.start_background_task("Remind me", "", delay_seconds=6)
    print(json.dumps({"workflow_id": started["workflow_id"], "started_at": time.time()}), flush=True)
    time.sleep(600)
elif mode == "start-approval":
    started = durable_tasks.start_background_task("Tidy desktop", "", wait_for_approval=True)
    wf = started["workflow_id"]
    while (durable_tasks.get_task_status(wf) or {}).get("state") != "waiting_for_approval":
        time.sleep(0.1)
    print(json.dumps({"workflow_id": wf}), flush=True)
    time.sleep(600)
elif mode == "start-and-shutdown":
    waiting = durable_tasks.start_background_task("Needs OK", "", wait_for_approval=True)
    delayed = durable_tasks.start_background_task("Much later", "", delay_seconds=3600)
    print(json.dumps({"approval": waiting["workflow_id"], "delayed": delayed["workflow_id"]}), flush=True)
    durable_tasks.shutdown()  # normal shutdown: must return and let the process exit
elif mode == "status":
    print(json.dumps(durable_tasks.get_task_status(sys.argv[3])), flush=True)
    durable_tasks.shutdown()
elif mode == "resume":
    wf = sys.argv[3]
    if len(sys.argv) > 4 and sys.argv[4] == "approve":
        assert durable_tasks.answer_approval(wf, True) == "ok"
    result = DBOS.retrieve_workflow(wf).get_result()
    print(json.dumps({"result": result, "finished_at": time.time()}), flush=True)
    durable_tasks.shutdown()
