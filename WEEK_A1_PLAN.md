# WEEK_A1_PLAN.md — Perfetto Baseline Analyzer (CLI → JSON) + TraceToy (Compose)

**Goal (locked):** Ship a working **Python CLI** that reads **one Perfetto trace** and writes `analysis.json` with:  
1) metadata, 2) startup window (best-effort), 3) long tasks (best-effort), 4) frame summary (best-effort).  
Also create a small **Compose sample app** to reliably generate traces.

## Guardrails (do not violate)
- **No AI/LLM** this week.
- **No dashboards/UI**.
- **One trace is enough**.
- If something isn’t reliably available in the trace, output `null` + an `"assumptions"` note.

## Definition of Done (DoD)
- `TraceToy` app builds and runs.
- You can record **one trace** and open it in Perfetto UI.
- `perfetto_agent` CLI runs on that trace and produces a valid `analysis.json`.
- `analysis.json` includes the required keys and non-empty values where possible.
- Minimal README explains how to run.

---

## Repo layout (target)
You can do this in one repo with two folders:

```
week-a1/
  perfetto-agent/
  TraceToy/
```

---

## Step-by-step tasks (execute one by one)

### Step 1 — Create folders + Python env (small)
**Changes**
- Create `week-a1/perfetto-agent/`
- Create venv + install deps

**Commands**
```bash
mkdir -p week-a1/perfetto-agent
cd week-a1/perfetto-agent
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install perfetto typer rich
```

**DoD**
- `python -c "import perfetto, typer, rich; print('ok')"` prints ok.

---

### Step 2 — Create the CLI skeleton (small)
**Create files**
```
perfetto-agent/
  perfetto_agent/
    __init__.py
    cli.py
    analyzer.py
  README.md
```

**CLI contract**
- Command:
  - `python -m perfetto_agent.cli analyze --trace <path> --out analysis.json`
- Options:
  - `--long-task-ms` default 50
  - `--top-n` default 5

**DoD**
- Running the command with a fake path prints a readable error.
- Running without args shows `--help`.

---

### Step 3 — Implement TraceProcessor wrapper utilities (small)
**Goal**
- In `analyzer.py`, implement:
  - open trace with `TraceProcessor(trace=trace_path)`
  - helper `_q(sql)` that returns list-of-dicts

**DoD**
- If trace path exists, it opens successfully (even if queries return empty).

---

### Step 4 — Implement output schema + “assumptions” (small)
**Schema (required top-level keys)**
- `trace_path`
- `trace_duration_ms`
- `processes` (list)
- `startup_ms` (nullable)
- `ui_thread_long_tasks` object:
  - `threshold_ms`, `count`, `top` (list)
- `frame_summary` object:
  - `total`, `janky` (nullable ok)
- `assumptions` object with short strings for each heuristic

**DoD**
- CLI writes JSON with these keys even if values are `null`.

---

### Step 5 — Metadata queries (small)
**Implement**
- `trace_duration_ms` (best-effort)
- `processes`: list a few processes/pids (best-effort)

**DoD**
- On a real trace, `processes` is non-empty (if not, keep it empty but no crash).

---

### Step 6 — Startup window heuristic (small)
**Implement (best-effort)**
- A simple heuristic like:
  - earliest slice timestamp → first `doFrame`/`Choreographer` occurrence  
  - if missing, set `startup_ms: null` and explain in assumptions

**DoD**
- Doesn’t crash if the slices aren’t present.

---

### Step 7 — Long task detection (small)
**Implement (best-effort)**
- Count slices with `dur_ms >= long_task_ms`
- Return top N by duration with `name`, `dur_ms`, `ts_ms`

**DoD**
- Produces count + top list without crashing.

*(Note: Perfect “main thread” attribution is Week A2; document that.)*

---

### Step 8 — Frame summary (small)
**Implement (best-effort)**
- Count `*doFrame*` slices as total frames (coarse)
- If `dur` exists, janky if `dur > 16ms` (coarse)
- If no frame slices exist, return `{total: null, janky: null}`

**DoD**
- Works on at least one trace; no crash on missing data.

---

### Step 9 — Create TraceToy (Compose) sample app (medium)
**Goal**
- A minimal Compose app that adds trace markers and generates:
  - startup init work
  - intentional UI stall button
  - optional background churn toggle
  - list scrolling

**DoD**
- Builds and runs on device/emulator.
- Markers appear in Perfetto trace (`StartupInit`, `UI#stall_...`, etc).

---

### Step 10 — Record one trace + verify in Perfetto UI (manual)
**Goal**
- Record a system trace while:
  - cold start TraceToy
  - scroll
  - press UI stall
  - toggle BG load and scroll

**DoD**
- You can open the trace in Perfetto UI and find your sections.

---

### Step 11 — Run analyzer on the trace (manual)
**Commands**
```bash
cd week-a1/perfetto-agent
source .venv/bin/activate
python -m perfetto_agent.cli analyze --trace /path/to/trace --out analysis.json
cat analysis.json
```

**DoD**
- `analysis.json` is valid JSON and includes required keys.
- At least one of: duration / processes / long task count / frame totals is non-null.

---

### Step 12 — README (small)
**Add**
- How to set up env
- How to run CLI
- Example output snippet (redact paths)
- How to record a trace (high-level)

**DoD**
- A new reader can reproduce Week A1.

---

## Suggested “one-step-at-a-time” prompt for Codex / Claude Code

For each step i (1..12), paste:

> You are implementing `WEEK_A1_PLAN.md` Step i.  
> Rules: (1) Do not expand scope beyond Step i. (2) Keep changes minimal. (3) Ensure the step DoD passes. (4) If a query/table is missing, handle gracefully and document an assumption.  
> Output: summary of changes + commands you ran + key diffs.
