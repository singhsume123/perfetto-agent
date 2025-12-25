# Perfetto Baseline Analyzer - Week A1

A Python CLI tool for analyzing Perfetto traces and extracting Android performance metrics.

## Project Structure

This is part of the Week A1 project which includes:
- `perfetto-agent/` - Python CLI analyzer (this directory)
- `TraceToy/` - Android Compose sample app for generating test traces

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

### Example

```bash
python3 -m perfetto_agent.cli analyze \
  --trace ~/traces/app_trace.pb \
  --out results.json \
  --long-task-ms 100 \
  --top-n 10
```

## Recording a Trace

### Using Android Studio Profiler

1. Open Android Studio
2. Open the TraceToy project from `../TraceToy/`
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
  "trace_path": "/path/to/trace.pb",
  "trace_duration_ms": 15234.5,
  "processes": [
    {"pid": 1234, "name": "com.example.tracetoy"},
    {"pid": 5678, "name": "system_server"}
  ],
  "startup_ms": 856.3,
  "ui_thread_long_tasks": {
    "threshold_ms": 50,
    "count": 12,
    "top": [
      {"name": "UI#stall_button_click", "dur_ms": 201.4, "ts_ms": 1234.5},
      {"name": "inflate", "dur_ms": 87.2, "ts_ms": 5678.9}
    ]
  },
  "frame_summary": {
    "total": 247,
    "janky": 8
  },
  "assumptions": {
    "trace_duration": "Calculated from trace_bounds table (end_ts - start_ts)",
    "processes": "Extracted from process table, limited to 20 entries",
    "startup": "Startup estimated as earliest slice to first Choreographer/doFrame",
    "long_tasks": "Long tasks detected as slices with dur >= 50ms...",
    "frames": "Frames counted from doFrame slices. Janky defined as dur > 16ms..."
  }
}
```

## Features

### Implemented (Week A1)

- Trace metadata extraction (duration, processes)
- Startup time estimation (best-effort heuristic)
- Long task detection (duration-based)
- Frame rendering summary (doFrame counting)
- Comprehensive assumptions documentation

### Limitations (to be addressed in future weeks)

- UI thread attribution is coarse (not precise main thread detection)
- Startup detection is basic (earliest slice to first frame)
- No AI/LLM analysis
- No dashboard/visualization
- Single trace analysis only

## Testing with TraceToy

1. Build and install the TraceToy app (see `../TraceToy/README.md`)
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

## Development

This is the Week A1 baseline implementation. Future weeks will add:
- More accurate thread attribution (Week A2)
- Additional performance metrics
- Multi-trace analysis
- Visualization capabilities
