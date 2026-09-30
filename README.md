# SARAH AI — VERSION 11

TO WHOMEVER IT CONCERNS

PLEASE BE NICE TO ME IM STILL NEW! XD

THIS IS MY 1ST ATTEMPT AT A CONVERSATIONAL/ CODING AI THAT USES GROQ (YOU CAN GET FREE API KEY FROM A ACCOUNT CREATION) AND OLLAMA AS THE OFFILE. THIS AI WAS MEANT
FOR IT TO CHALLENGE MYSELF AS A PROGRAMMER AS WELL MAKE THIS AI WITH NO MONEY BEING SPENT ON TOKENS OR OTHER TOOLS. WITH THAT BEING SAID PLEASE BE GENTLE WITH MY CODING ITS NOT ALL GONNA 
MAKE SENSE AND YES SOME PARTS I HAD AI CREATE DUE TO CONFUSING BUGS I KEPT GETTING WITH WINDOWS 10 AND ODD PERMISSIONS ON MY COMPUTER. IF YOU HAVE QUESTIONS FEEL FREE TO ASK 
I'M WILLING TO HELP THE BEST I CAN WITH THE LIMITED KNOWLEDGE I HAVE. 

IF YOU HAVE QUESTIONS ABOUT FILE STRUCTURES FEEL FREE TO ASK AND ILL DO MY BEST TO HELP

REQUIREMENTS: 
GROQ KEY (GOES INSIDE SARAH_CORE.PY SINCE IT'S HARD CODED)
NODE MODUELS
CUBSIM
PIXI + LIVEPIXI

---

## WHAT'S NEW IN V11

All new features live in their own files and switch themselves off if a library is missing.
Check `GET /api/v11/status` to see what's running.

| Feature | File | Needs |
|---|---|---|
| Background tasks that survive crashes/restarts, can wait, or wait for your OK | `durable_tasks.py` | `pip install dbos` |
| Procedure memory: remembers HOW she finished tasks and reuses/retires recipes | `procedure_memory.py` | nothing extra |
| Proactive suggestions that learn from accepted / rejected / ignored offers | `proactive_engine.py` | nothing extra |
| Screen timeline + "what's happened so far" summaries | `screen_timeline.py` | nothing extra |
| Read PDFs, Word, PowerPoint, Excel and scanned documents | `document_reader.py` | `pip install docling` (large) |

### New API endpoints (backend on port 8907)

**Background tasks** ("handle this and get back to me")
- `POST /api/durable_tasks` `{"title": "...", "instructions": "...", "delay_seconds": 0, "wait_for_approval": false}`
- `GET /api/durable_tasks` / `GET /api/durable_tasks/{workflow_id}`
- `POST /api/durable_tasks/{workflow_id}/approve` `{"approve": true}`
- `POST /api/durable_tasks/{workflow_id}/feedback` `{"good": true}` (teaches procedure memory)
- `GET /api/notifications` then `POST /api/notifications/{id}/read` (poll this like `/api/wake`)
- A task's `state` is one of: `waiting_for_approval`, `scheduled`, `working`, `done`, `failed`, `cancelled`
- Waiting never blocks anything: closing Sarah is instant, and scheduled / waiting tasks pick back up next time she starts

**Procedure memory**: `GET /api/procedures`, `GET /api/procedures/search?task=...`, `POST /api/procedures/{id}/feedback`

**Proactive suggestions**
- `/api/screen/analyze_last` now also returns `proactive_offer` + `suggestion_id`. Only speak the suggestion when `proactive_offer` is true.
- Report the reaction: `POST /api/proactive/{suggestion_id}/feedback` `{"outcome": "accepted" | "rejected" | "ignored"}`
- `POST /api/proactive/check` `{"category": "...", "text": "..."}`, `GET /api/proactive/stats`

**Screen timeline**: `GET /api/screen/timeline`, `POST /api/screen/timeline/summary`, `DELETE /api/screen/timeline`

**Documents**: `POST /api/documents/read` `{"source": "C:/path/file.pdf"}`, `POST /api/documents/ask` `{"source": "...", "question": "..."}`
(The first Docling read downloads its models and takes a while, after that it works offline.)

### 3D avatar (VRM + Mixamo)
A 3D anime Sarah with facial expressions, lip sync and full-body animations, served at
**http://127.0.0.1:8907/avatar/**. Setup, the list of Mixamo animations to download, and
Electron instructions are in [`renderer/avatar3d/README.md`](renderer/avatar3d/README.md).

### Tests
```
pip install pytest httpx dbos
python -m pytest tests
```
Includes crash tests that kill the backend mid-task and check the task picks back up.
