# Perfetto Baseline Analyzer

A Python CLI tool for analyzing Perfetto traces and extracting Android performance metrics.

## Generating Test Traces

Use the [TraceToy](https://github.com/singhsume123/TraceToy) Android app to generate Perfetto traces for testing. TraceToy is a Jetpack Compose sample app that includes UI stall buttons, scrollable lists, and custom trace markers.

## Setup

### 1. Create Python Environment

```bash
cd perfetto-agent
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install Dependencies

```bash
pip install --upgrade pip
pip install perfetto typer rich
```

### 3. Verify Installation

```bash
python3 -c "import perfetto, typer, rich; print('All dependencies installed successfully')"
```

## Usage

### Basic Analysis

```bash
# Activate virtual environment
source .venv/bin/activate

# Analyze a trace
python3 -m perfetto_agent.cli analyze --trace /path/to/trace.pb --out analysis.json

# View results
cat analysis.json
```

### Command Options

- `--trace PATH` - Path to Perfetto trace file (required)
- `--out PATH` - Output JSON file path (default: `analysis.json`)
- `--long-task-ms INT` - Threshold for long tasks in milliseconds (default: `50`)
- `--top-n INT` - Number of top long tasks to report (default: `5`)
- `--focus-process TEXT` - Filter analysis to a specific process name (optional)
- `--schema-version TEXT` - Schema version to emit (default: `A2`)

### Example

```bash
python3 -m perfetto_agent.cli analyze \
  --trace ~/traces/app_trace.pb \
  --out results.json \
  --long-task-ms 100 \
  --top-n 10 \
  --focus-process com.example.tracetoy
```

## Recording a Trace

### Using Android Studio Profiler

1. Open Android Studio
2. Clone and open the [TraceToy](https://github.com/singhsume123/TraceToy) app
3. Build and run the app on a device/emulator
4. Open Profiler (View > Tool Windows > Profiler)
5. Click the "+" button and select your device/app
6. Click "CPU" and select "System Trace"
7. Click "Record"
8. Perform actions in the app (tap UI Stall button, scroll list, etc.)
9. Click "Stop"
10. Export the trace (right-click > Export trace)

### Using Command Line (adb)

```bash
# Start recording
adb shell perfetto \
  -c - --txt \
  -o /data/misc/perfetto-traces/trace \
  <<EOF
buffers: {
    size_kb: 63488
    fill_policy: DISCARD
}
data_sources: {
    config {
        name: "linux.ftrace"
        ftrace_config {
            ftrace_events: "sched/sched_switch"
            ftrace_events: "power/suspend_resume"
            ftrace_events: "sched/sched_wakeup"
            ftrace_events: "sched/sched_waking"
            atrace_categories: "gfx"
            atrace_categories: "view"
            atrace_categories: "webview"
            atrace_categories: "camera"
            atrace_categories: "dalvik"
            atrace_categories: "input"
        }
    }
}
duration_ms: 20000
EOF

# Pull the trace
adb pull /data/misc/perfetto-traces/trace trace.pb
```

## Output Schema

The analyzer produces a JSON file with the following structure:

```json
{
  "schema_version": "A2",
  "focus_process": "com.example.tracetoy",
  "focus_pid": 1234,
  "trace_path": "/path/to/trace.pb",
  "trace_duration_ms": 15234.5,
  "processes": [
    {"pid": 1234, "name": "com.example.tracetoy"},
    {"pid": 5678, "name": "system_server"}
  ],
  "startup_ms": 856.3,
  "threads": {
    "main_thread": {"tid": 1234, "name": "main", "pid": 1234, "process_name": "com.example.tracetoy"},
    "top_threads_by_slice_ms": [
      {"tid": 1234, "thread_name": "main", "pid": 1234, "total_slice_ms": 987.6}
    ]
  },
  "ui_thread_long_tasks": {
    "threshold_ms": 50,
    "count": 12,
    "top": [
      {"name": "UI#stall_button_click", "dur_ms": 201.4, "ts_ms": 1234.5},
      {"name": "inflate", "dur_ms": 87.2, "ts_ms": 5678.9}
    ]
  },
  "features": {
    "app_sections": {
      "counts": {"StartupInit": 1, "UI#stall_button_click": 2},
      "top_by_total_ms": [
        {"name": "UI#stall_button_click", "total_ms": 402.1, "count": 2}
      ]
    },
    "long_slices_attributed": {
      "threshold_ms": 50,
      "count": 12,
      "top": [
        {"name": "UI#stall_button_click", "dur_ms": 201.4, "pid": 1234, "tid": 1234, "thread_name": "main", "process_name": "com.example.tracetoy"}
      ]
    },
    "cpu_features": {
      "top_processes_by_slice_ms": [
        {"pid": 1234, "process_name": "com.example.tracetoy", "total_slice_ms": 4567.8}
      ],
      "top_threads_by_slice_ms": [
        {"tid": 1234, "thread_name": "main", "pid": 1234, "total_slice_ms": 987.6}
      ]
    },
    "frame_features": {
      "total_frames": 247,
      "janky_frames": 8,
      "p95_frame_ms": 22.3
    }
  },
  "summary": {
    "main_thread_found": true,
    "top_app_sections": ["UI#stall_button_click", "StartupInit"],
    "top_long_slice_name": "UI#stall_button_click"
  },
  "assumptions": {
    "trace_duration": "Calculated from trace_bounds table (end_ts - start_ts)",
    "processes": "Extracted from process table, limited to 20 entries",
    "startup": "Startup estimated as earliest slice to first Choreographer/doFrame",
    "long_tasks": "Long tasks detected as slices with dur >= 50ms...",
    "frames": "Frame features computed from doFrame slices (p95/jank best-effort)"
  }
}
```

## Features

- Trace metadata extraction (duration, processes)
- Startup time estimation (best-effort heuristic)
- Long task detection (duration-based)
- Frame rendering features (p95 + jank)
- App marker extraction (Trace.beginSection-style)
- Slice attribution to process/thread
- Frame p95 duration and CPU-ish aggregates
- A3 core: work classification into app/framework/system/unknown with breakdowns
- Comprehensive assumptions documentation

### Current Limitations

- UI thread attribution is best-effort, depends on available tables
- Startup detection is basic (earliest slice to first frame)
- Classification is token-based and conservative; unknown used when uncertain
- No AI/LLM analysis
- No dashboard/visualization
- Single trace analysis only

## A3 Core: Work Classification

The analyzer classifies slice work into four categories:
- `app` (app markers on the focused process)
- `framework` (framework tokens on the focused process)
- `system` (non-focused pids or system tokens)
- `unknown` (fallback)

These categories appear on `features.long_slices_attributed.top[*].category`,
with aggregate totals in `features.work_breakdown` and summary fields in
`summary.dominant_work_category` and `summary.main_thread_blocked_by`.

## Testing with TraceToy

1. Build and install the [TraceToy](https://github.com/singhsume123/TraceToy) app
2. Record a trace while using the app
3. Run the analyzer on the recorded trace
4. Verify the output includes:
   - TraceToy process
   - Startup markers
   - UI stall events (200ms)
   - Frame rendering data

## Troubleshooting

### "Module not found" error
Make sure you've activated the virtual environment:
```bash
source .venv/bin/activate
```

### "Trace file not found" error
Verify the trace file path is correct and the file exists:
```bash
ls -lh /path/to/trace.pb
```

### Empty results
The trace might not contain expected data. Try:
- Recording a longer trace
- Using the TraceToy app to generate known trace events
- Checking the `assumptions` field in the output for hints
