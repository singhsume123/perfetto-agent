"""Core analysis logic for Perfetto traces."""

from perfetto.trace_processor import TraceProcessor


def _q(tp: TraceProcessor, sql: str) -> list[dict]:
    """Execute a SQL query and return results as a list of dictionaries."""
    result = tp.query(sql)
    rows = []
    for row in result:
        row_dict = {col: getattr(row, col) for col in result.column_names}
        rows.append(row_dict)
    return rows


def _safe_q(tp: TraceProcessor, sql: str, assumption_key: str, assumptions: dict | None) -> list[dict]:
    """Execute a SQL query, returning [] on failure and recording the reason."""
    try:
        return _q(tp, sql)
    except Exception as exc:
        if assumptions is not None and assumption_key not in assumptions:
            assumptions[assumption_key] = f"Query failed for {assumption_key}: {str(exc)}"
        return []


def _set_assumption(assumptions: dict, key: str, note: str) -> None:
    if key not in assumptions:
        assumptions[key] = note


class PerfettoAnalyzer:
    """Wrapper for Perfetto TraceProcessor with helper utilities."""

    def __init__(self, trace_path: str):
        """
        Initialize the analyzer with a trace file.

        Args:
            trace_path: Path to the Perfetto trace file
        """
        self.trace_path = trace_path
        self.tp = TraceProcessor(trace=trace_path)

    def close(self):
        """Close the trace processor."""
        self.tp.close()

    def get_trace_duration_ms(self, assumptions: dict) -> float | None:
        """
        Get the total duration of the trace in milliseconds.

        Returns:
            Duration in milliseconds or None if not available
        """
        rows = _safe_q(
            self.tp,
            "SELECT (end_ts - start_ts) / 1e6 AS duration_ms FROM trace_bounds",
            "trace_duration",
            assumptions
        )
        if rows and len(rows) > 0:
            return rows[0].get("duration_ms")
        return None

    def get_processes(self, assumptions: dict) -> list[dict]:
        """
        Get list of processes from the trace.

        Returns:
            List of process dictionaries with pid and name
        """
        rows = _safe_q(
            self.tp,
            """
            SELECT DISTINCT pid, name
            FROM process
            WHERE pid IS NOT NULL AND name IS NOT NULL
            ORDER BY pid
            LIMIT 20
            """,
            "processes",
            assumptions
        )
        return [{"pid": row["pid"], "name": row["name"]} for row in rows]

    def get_startup_ms(self, assumptions: dict) -> tuple[float | None, str]:
        """
        Estimate app startup time using a simple heuristic.

        Heuristic: Time from earliest slice to first Choreographer/doFrame occurrence.

        Returns:
            Tuple of (startup_ms, assumption_note)
        """
        # Get earliest slice timestamp
        earliest = _safe_q(
            self.tp,
            "SELECT MIN(ts) / 1e6 AS earliest_ms FROM slice",
            "startup",
            assumptions
        )
        if not earliest or earliest[0].get("earliest_ms") is None:
            return None, "No slices found in trace"

        earliest_ms = earliest[0]["earliest_ms"]

        # Find first Choreographer/doFrame slice
        first_frame = _safe_q(
            self.tp,
            """
            SELECT MIN(ts) / 1e6 AS first_frame_ms
            FROM slice
            WHERE name LIKE '%Choreographer%' OR name LIKE '%doFrame%'
            """,
            "startup",
            assumptions
        )

        if not first_frame or first_frame[0].get("first_frame_ms") is None:
            return None, "No Choreographer/doFrame slices found for startup detection"

        first_frame_ms = first_frame[0]["first_frame_ms"]
        startup_duration = first_frame_ms - earliest_ms

        assumption = (
            "Startup estimated as earliest slice "
            f"({earliest_ms:.2f}ms) to first Choreographer/doFrame ({first_frame_ms:.2f}ms)"
        )
        return startup_duration, assumption

    def get_long_tasks(self, threshold_ms: int, top_n: int, assumptions: dict) -> tuple[int, list[dict], str]:
        """
        Detect long-running tasks based on slice duration.

        Args:
            threshold_ms: Minimum duration to consider a task "long"
            top_n: Number of top tasks to return

        Returns:
            Tuple of (total_count, top_tasks_list, assumption_note)
        """
        # Count all slices that exceed the threshold
        count_result = _safe_q(
            self.tp,
            f"""
            SELECT COUNT(*) AS count
            FROM slice
            WHERE dur / 1e6 >= {threshold_ms}
            """,
            "long_tasks",
            assumptions
        )

        total_count = count_result[0]["count"] if count_result else 0

        # Get top N longest tasks
        top_tasks = _safe_q(
            self.tp,
            f"""
            SELECT
                name,
                dur / 1e6 AS dur_ms,
                ts / 1e6 AS ts_ms
            FROM slice
            WHERE dur / 1e6 >= {threshold_ms}
            ORDER BY dur DESC
            LIMIT {top_n}
            """,
            "long_tasks",
            assumptions
        )

        top_list = [
            {
                "name": task["name"],
                "dur_ms": task["dur_ms"],
                "ts_ms": task["ts_ms"]
            }
            for task in top_tasks
        ]

        assumption = (
            f"Long tasks detected as slices with dur >= {threshold_ms}ms. "
            "Note: UI thread attribution not yet implemented (planned for Week A2)"
        )
        return total_count, top_list, assumption

    def get_frame_summary(self, assumptions: dict) -> tuple[int | None, int | None, str]:
        """
        Get summary of frame rendering performance.

        Heuristic: Count slices with 'doFrame' in name, janky if duration > 16ms.

        Returns:
            Tuple of (total_frames, janky_frames, assumption_note)
        """
        # Count total doFrame slices
        total_result = _safe_q(
            self.tp,
            """
            SELECT COUNT(*) AS total
            FROM slice
            WHERE name LIKE '%doFrame%'
            """,
            "frames",
            assumptions
        )

        total_frames = total_result[0]["total"] if total_result else 0

        if total_frames == 0:
            return None, None, "No doFrame slices found in trace"

        # Count janky frames (duration > 16ms, which is ~60fps)
        janky_result = _safe_q(
            self.tp,
            """
            SELECT COUNT(*) AS janky
            FROM slice
            WHERE name LIKE '%doFrame%' AND dur / 1e6 > 16
            """,
            "frames",
            assumptions
        )

        janky_frames = janky_result[0]["janky"] if janky_result else 0

        assumption = (
            "Frames counted from doFrame slices. Janky defined as dur > 16ms (60fps threshold). "
            f"Found {total_frames} total frames, {janky_frames} janky"
        )
        return total_frames, janky_frames, assumption


def analyze_trace(
    trace_path: str,
    long_task_ms: int,
    top_n: int,
    focus_process: str | None,
    schema_version: str
) -> dict:
    """
    Analyze a Perfetto trace and return structured results.

    Args:
        trace_path: Path to the trace file
        long_task_ms: Threshold for identifying long tasks
        top_n: Number of top long tasks to include

    Returns:
        Dictionary with analysis results following the required schema
    """
    analyzer = PerfettoAnalyzer(trace_path)

    try:
        assumptions: dict = {}

        # Extract metadata
        trace_duration_ms = analyzer.get_trace_duration_ms(assumptions)
        processes = analyzer.get_processes(assumptions)

        # Extract startup time
        startup_ms, startup_assumption = analyzer.get_startup_ms(assumptions)

        # Extract long tasks
        long_task_count, long_task_top, long_task_assumption = analyzer.get_long_tasks(
            long_task_ms,
            top_n,
            assumptions
        )

        # Extract frame summary
        frame_total, frame_janky, frame_assumption = analyzer.get_frame_summary(assumptions)

        # Initialize result with required schema
        result = {
            "schema_version": schema_version,
            "focus_process": focus_process,
            "focus_pid": None,
            "trace_path": trace_path,
            "trace_duration_ms": trace_duration_ms,
            "processes": processes,
            "startup_ms": startup_ms,
            "ui_thread_long_tasks": {
                "threshold_ms": long_task_ms,
                "count": long_task_count,
                "top": long_task_top
            },
            "frame_summary": {
                "total": frame_total,
                "janky": frame_janky
            },
            "assumptions": assumptions
        }
        _set_assumption(
            result["assumptions"],
            "trace_duration",
            "Calculated from trace_bounds table (end_ts - start_ts)"
        )
        _set_assumption(
            result["assumptions"],
            "processes",
            "Extracted from process table, limited to 20 entries"
        )
        _set_assumption(result["assumptions"], "startup", startup_assumption)
        _set_assumption(result["assumptions"], "long_tasks", long_task_assumption)
        _set_assumption(result["assumptions"], "frames", frame_assumption)
        return result
    finally:
        analyzer.close()
