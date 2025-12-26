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


def _normalize_slice_name(name: str | None) -> str:
    if not name:
        return "<internal slice>"
    stripped = name.strip()
    if stripped.isdigit():
        return "<internal slice>"
    return name


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    sorted_values = sorted(values)
    index = int(round((len(sorted_values) - 1) * percentile))
    return sorted_values[index]


def classify_slice_name(name: str | None, pid: int | None, focus_pid: int | None) -> str:
    """
    Classify a slice into app/framework/system/unknown using pid + name tokens.
    """
    if focus_pid is not None and pid is not None and pid != focus_pid:
        return "system"

    if not name:
        return "unknown"

    lower_name = name.lower()
    app_tokens = ["ui#", "bg#", "startupinit"]
    framework_tokens = [
        "choreographer",
        "doframe",
        "renderthread",
        "viewrootimpl",
        "dequeuebuffer",
        "blast",
        "hwui"
    ]
    system_tokens = ["binder", "surfaceflinger", "sched", "kworker", "irq", "futex"]

    if focus_pid is not None and pid == focus_pid:
        if any(token in lower_name for token in app_tokens):
            return "app"
        if any(token in lower_name for token in framework_tokens):
            return "framework"

    if any(token in lower_name for token in system_tokens):
        return "system"

    return "unknown"


def _dominant_category(category_totals: dict[str, float]) -> tuple[str | None, str | None]:
    if not category_totals:
        return None, "No category totals available"
    max_value = max(category_totals.values())
    if max_value <= 0:
        return None, "Category totals are all zero"
    winners = [key for key, value in category_totals.items() if value == max_value]
    if len(winners) != 1:
        return None, "Category totals have a tie"
    return winners[0], None


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

    def resolve_focus_pid(self, focus_process: str | None, assumptions: dict) -> int | None:
        """
        Resolve focus_process to a PID, preferring the busiest matching process.
        """
        if not focus_process:
            return None

        escaped = focus_process.replace("'", "''")
        candidates = _safe_q(
            self.tp,
            f"""
            SELECT pid, name
            FROM process
            WHERE name = '{escaped}'
            """,
            "focus_process",
            assumptions
        )

        if not candidates:
            return None

        if len(candidates) == 1:
            return candidates[0].get("pid")

        ranked = _safe_q(
            self.tp,
            f"""
            SELECT
                p.pid AS pid,
                COUNT(s.id) AS slice_count,
                MAX(s.ts) AS max_ts
            FROM process p
            JOIN thread t ON t.upid = p.upid
            JOIN thread_track tt ON tt.utid = t.utid
            JOIN slice s ON s.track_id = tt.id
            WHERE p.name = '{escaped}'
            GROUP BY p.pid
            ORDER BY slice_count DESC, max_ts DESC
            LIMIT 1
            """,
            "focus_process",
            assumptions
        )

        if ranked:
            return ranked[0].get("pid")

        return candidates[0].get("pid")

    def resolve_main_thread(self, focus_pid: int | None, assumptions: dict) -> dict | None:
        """
        Resolve the main thread for a focus PID using best-effort heuristics.
        """
        if focus_pid is None:
            return None

        main_by_name = _safe_q(
            self.tp,
            f"""
            SELECT
                t.tid AS tid,
                t.name AS name,
                p.pid AS pid,
                p.name AS process_name
            FROM thread t
            JOIN process p ON t.upid = p.upid
            WHERE p.pid = {focus_pid} AND t.name = 'main'
            LIMIT 1
            """,
            "main_thread",
            assumptions
        )

        if main_by_name:
            return {
                "tid": main_by_name[0].get("tid"),
                "name": main_by_name[0].get("name"),
                "pid": main_by_name[0].get("pid"),
                "process_name": main_by_name[0].get("process_name")
            }

        main_by_tid = _safe_q(
            self.tp,
            f"""
            SELECT
                t.tid AS tid,
                t.name AS name,
                p.pid AS pid,
                p.name AS process_name
            FROM thread t
            JOIN process p ON t.upid = p.upid
            WHERE p.pid = {focus_pid} AND t.tid = p.pid
            LIMIT 1
            """,
            "main_thread",
            assumptions
        )

        if main_by_tid:
            return {
                "tid": main_by_tid[0].get("tid"),
                "name": main_by_tid[0].get("name"),
                "pid": main_by_tid[0].get("pid"),
                "process_name": main_by_tid[0].get("process_name")
            }

        return None

    def _query_long_slices_attributed(
        self,
        threshold_ms: int,
        top_n: int,
        pid_filter: int | None,
        tid_filter: int | None,
        assumptions: dict,
        assumption_key: str
    ) -> tuple[int, list[dict]]:
        where_clauses = [f"s.dur / 1e6 >= {threshold_ms}"]
        if pid_filter is not None:
            where_clauses.append(f"p.pid = {pid_filter}")
        if tid_filter is not None:
            where_clauses.append(f"t.tid = {tid_filter}")
        where_sql = " AND ".join(where_clauses)

        count_rows = _safe_q(
            self.tp,
            f"""
            SELECT COUNT(*) AS count
            FROM slice s
            JOIN track tr ON s.track_id = tr.id
            JOIN thread_track tt ON tt.id = tr.id
            JOIN thread t ON t.utid = tt.utid
            JOIN process p ON p.upid = t.upid
            WHERE {where_sql}
            """,
            assumption_key,
            assumptions
        )
        count = count_rows[0]["count"] if count_rows else 0

        top_rows = _safe_q(
            self.tp,
            f"""
            SELECT
                s.name AS name,
                s.dur / 1e6 AS dur_ms,
                s.ts / 1e6 AS ts_ms,
                p.pid AS pid,
                t.tid AS tid,
                t.name AS thread_name,
                p.name AS process_name
            FROM slice s
            JOIN track tr ON s.track_id = tr.id
            JOIN thread_track tt ON tt.id = tr.id
            JOIN thread t ON t.utid = tt.utid
            JOIN process p ON p.upid = t.upid
            WHERE {where_sql}
            ORDER BY s.dur DESC
            LIMIT {top_n}
            """,
            assumption_key,
            assumptions
        )

        top = []
        for row in top_rows:
            top.append(
                {
                    "name": _normalize_slice_name(row.get("name")),
                    "dur_ms": row.get("dur_ms"),
                    "ts_ms": row.get("ts_ms"),
                    "pid": row.get("pid"),
                    "tid": row.get("tid"),
                    "thread_name": row.get("thread_name"),
                    "process_name": row.get("process_name")
                }
            )
        return count, top

    def _query_attributed_slices(
        self,
        threshold_ms: int,
        pid_filter: int | None,
        tid_filter: int | None,
        assumptions: dict,
        assumption_key: str
    ) -> list[dict]:
        where_clauses = [f"s.dur / 1e6 >= {threshold_ms}"]
        if pid_filter is not None:
            where_clauses.append(f"p.pid = {pid_filter}")
        if tid_filter is not None:
            where_clauses.append(f"t.tid = {tid_filter}")
        where_sql = " AND ".join(where_clauses)

        return _safe_q(
            self.tp,
            f"""
            SELECT
                s.name AS name,
                s.dur / 1e6 AS dur_ms,
                p.pid AS pid,
                t.tid AS tid
            FROM slice s
            JOIN track tr ON s.track_id = tr.id
            JOIN thread_track tt ON tt.id = tr.id
            JOIN thread t ON t.utid = tt.utid
            JOIN process p ON p.upid = t.upid
            WHERE {where_sql}
            """,
            assumption_key,
            assumptions
        )

    def get_long_slices_attributed(
        self,
        threshold_ms: int,
        top_n: int,
        focus_pid: int | None,
        assumptions: dict
    ) -> dict:
        count, top = self._query_long_slices_attributed(
            threshold_ms,
            top_n,
            focus_pid,
            None,
            assumptions,
            "long_slices_attributed"
        )
        top_payload = [
            {
                "name": item["name"],
                "dur_ms": item["dur_ms"],
                "pid": item["pid"],
                "tid": item["tid"],
                "thread_name": item["thread_name"],
                "process_name": item["process_name"],
                "category": classify_slice_name(item["name"], item["pid"], focus_pid)
            }
            for item in top
        ]
        return {
            "threshold_ms": threshold_ms,
            "count": count,
            "top": top_payload
        }

    def get_work_breakdown(
        self,
        threshold_ms: int,
        focus_pid: int | None,
        main_thread: dict | None,
        assumptions: dict
    ) -> dict:
        rows = self._query_attributed_slices(
            threshold_ms,
            focus_pid,
            None,
            assumptions,
            "work_breakdown"
        )

        if not rows:
            _set_assumption(
                assumptions,
                "work_breakdown",
                "No attributed slices available for work breakdown"
            )
            by_category_ms: dict[str, float] = {}
        else:
            by_category_ms = {
                "app": 0.0,
                "framework": 0.0,
                "system": 0.0,
                "unknown": 0.0
            }
            for row in rows:
                dur_ms = row.get("dur_ms")
                if dur_ms is None:
                    continue
                category = classify_slice_name(row.get("name"), row.get("pid"), focus_pid)
                by_category_ms[category] += float(dur_ms)

        if not main_thread or not main_thread.get("tid"):
            _set_assumption(
                assumptions,
                "main_thread_blocking",
                "Main thread unavailable for blocking breakdown"
            )
            main_thread_blocking: dict[str, float] = {}
        else:
            main_rows = self._query_attributed_slices(
                threshold_ms,
                None,
                main_thread.get("tid"),
                assumptions,
                "main_thread_blocking"
            )
            if not main_rows:
                main_thread_blocking = {}
            else:
                main_thread_blocking = {
                    "app_ms": 0.0,
                    "framework_ms": 0.0,
                    "system_ms": 0.0,
                    "unknown_ms": 0.0
                }
                for row in main_rows:
                    dur_ms = row.get("dur_ms")
                    if dur_ms is None:
                        continue
                    category = classify_slice_name(row.get("name"), row.get("pid"), focus_pid)
                    key = f"{category}_ms"
                    main_thread_blocking[key] += float(dur_ms)

        return {
            "by_category_ms": by_category_ms,
            "main_thread_blocking": main_thread_blocking
        }

    def get_ui_thread_long_tasks(
        self,
        threshold_ms: int,
        top_n: int,
        main_thread: dict | None,
        focus_pid: int | None,
        assumptions: dict
    ) -> tuple[int, list[dict], str]:
        if main_thread:
            count, top = self._query_long_slices_attributed(
                threshold_ms,
                top_n,
                None,
                main_thread.get("tid"),
                assumptions,
                "long_tasks"
            )
            assumption = f"Long tasks filtered to main thread tid={main_thread.get('tid')}"
        elif focus_pid is not None:
            count, top = self._query_long_slices_attributed(
                threshold_ms,
                top_n,
                focus_pid,
                None,
                assumptions,
                "long_tasks"
            )
            assumption = f"Long tasks filtered to focus pid={focus_pid}"
        else:
            count, top = self._query_long_slices_attributed(
                threshold_ms,
                top_n,
                None,
                None,
                assumptions,
                "long_tasks"
            )
            assumption = "Long tasks computed across all slices (no focus process or main thread)"

        top_list = [
            {
                "name": item["name"],
                "dur_ms": item["dur_ms"],
                "ts_ms": item["ts_ms"]
            }
            for item in top
        ]
        return count, top_list, assumption

    def get_app_sections(self, focus_pid: int | None, assumptions: dict) -> dict:
        """
        Extract app-defined sections from slices using simple heuristics.
        """
        where_clauses = ["(s.name LIKE '%#%' OR s.name IN ('StartupInit'))"]
        if focus_pid is not None:
            where_clauses.append(f"p.pid = {focus_pid}")
        where_sql = " AND ".join(where_clauses)

        rows = _safe_q(
            self.tp,
            f"""
            SELECT
                s.name AS name,
                COUNT(*) AS count,
                SUM(s.dur) / 1e6 AS total_ms
            FROM slice s
            JOIN track tr ON s.track_id = tr.id
            JOIN thread_track tt ON tt.id = tr.id
            JOIN thread t ON t.utid = tt.utid
            JOIN process p ON p.upid = t.upid
            WHERE {where_sql}
            GROUP BY s.name
            ORDER BY total_ms DESC
            LIMIT 20
            """,
            "app_sections",
            assumptions
        )

        counts: dict[str, int] = {}
        top_by_total_ms = []
        for row in rows:
            name = _normalize_slice_name(row.get("name"))
            counts[name] = row.get("count") or 0
            top_by_total_ms.append(
                {
                    "name": name,
                    "total_ms": row.get("total_ms"),
                    "count": row.get("count")
                }
            )

        return {
            "counts": counts,
            "top_by_total_ms": top_by_total_ms
        }

    def get_frame_features(self, assumptions: dict) -> dict:
        """
        Compute frame feature aggregates including p95 duration.
        """
        rows = _safe_q(
            self.tp,
            """
            SELECT dur / 1e6 AS dur_ms
            FROM slice
            WHERE name LIKE '%doFrame%'
            """,
            "frames",
            assumptions
        )

        if not rows:
            _set_assumption(assumptions, "frames", "No doFrame slices found for frame features")
            return {
                "total_frames": None,
                "janky_frames": None,
                "p95_frame_ms": None
            }

        durations = [row["dur_ms"] for row in rows if row.get("dur_ms") is not None]
        if not durations:
            _set_assumption(assumptions, "frames", "Frame durations unavailable for p95 calculation")
            return {
                "total_frames": None,
                "janky_frames": None,
                "p95_frame_ms": None
            }

        total_frames = len(durations)
        janky_frames = len([value for value in durations if value > 16])
        p95_frame_ms = _percentile(durations, 0.95)

        return {
            "total_frames": total_frames,
            "janky_frames": janky_frames,
            "p95_frame_ms": p95_frame_ms
        }

    def get_cpu_features(self, focus_pid: int | None, assumptions: dict) -> dict:
        """
        Compute CPU-ish aggregates using slice duration totals.
        """
        process_filter = ""
        if focus_pid is not None:
            process_filter = f"WHERE p.pid = {focus_pid}"

        process_rows = _safe_q(
            self.tp,
            f"""
            SELECT
                p.pid AS pid,
                p.name AS process_name,
                SUM(s.dur) / 1e6 AS total_slice_ms
            FROM slice s
            JOIN track tr ON s.track_id = tr.id
            JOIN thread_track tt ON tt.id = tr.id
            JOIN thread t ON t.utid = tt.utid
            JOIN process p ON p.upid = t.upid
            {process_filter}
            GROUP BY p.pid, p.name
            ORDER BY total_slice_ms DESC
            LIMIT 10
            """,
            "cpu_features",
            assumptions
        )

        thread_filter = ""
        if focus_pid is not None:
            thread_filter = f"WHERE p.pid = {focus_pid}"

        thread_rows = _safe_q(
            self.tp,
            f"""
            SELECT
                t.tid AS tid,
                t.name AS thread_name,
                p.pid AS pid,
                SUM(s.dur) / 1e6 AS total_slice_ms
            FROM slice s
            JOIN track tr ON s.track_id = tr.id
            JOIN thread_track tt ON tt.id = tr.id
            JOIN thread t ON t.utid = tt.utid
            JOIN process p ON p.upid = t.upid
            {thread_filter}
            GROUP BY t.tid, t.name, p.pid
            ORDER BY total_slice_ms DESC
            LIMIT 10
            """,
            "cpu_features",
            assumptions
        )

        top_processes = [
            {
                "pid": row.get("pid"),
                "process_name": row.get("process_name"),
                "total_slice_ms": row.get("total_slice_ms")
            }
            for row in process_rows
        ]
        top_threads = [
            {
                "tid": row.get("tid"),
                "thread_name": row.get("thread_name"),
                "pid": row.get("pid"),
                "total_slice_ms": row.get("total_slice_ms")
            }
            for row in thread_rows
        ]

        return {
            "top_processes_by_slice_ms": top_processes,
            "top_threads_by_slice_ms": top_threads
        }
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

        focus_pid = analyzer.resolve_focus_pid(focus_process, assumptions)
        main_thread = analyzer.resolve_main_thread(focus_pid, assumptions)

        # Extract startup time
        startup_ms, startup_assumption = analyzer.get_startup_ms(assumptions)

        # Extract long tasks with attribution
        long_task_count, long_task_top, long_task_assumption = analyzer.get_ui_thread_long_tasks(
            long_task_ms,
            top_n,
            main_thread,
            focus_pid,
            assumptions
        )
        long_slices_attributed = analyzer.get_long_slices_attributed(
            long_task_ms,
            top_n,
            focus_pid,
            assumptions
        )
        app_sections = analyzer.get_app_sections(focus_pid, assumptions)

        # Extract frame features
        frame_features = analyzer.get_frame_features(assumptions)
        cpu_features = analyzer.get_cpu_features(focus_pid, assumptions)
        work_breakdown = analyzer.get_work_breakdown(long_task_ms, focus_pid, main_thread, assumptions)

        # Initialize result with required schema
        top_app_sections = [
            entry["name"]
            for entry in app_sections.get("top_by_total_ms", [])
            if entry.get("name")
        ][:3]
        top_long_slice_name = None
        if long_slices_attributed.get("top"):
            top_long_slice_name = long_slices_attributed["top"][0].get("name")

        dominant_work_category, dominant_reason = _dominant_category(
            work_breakdown.get("by_category_ms", {})
        )
        if dominant_reason:
            _set_assumption(assumptions, "dominant_work_category", dominant_reason)

        main_thread_blocked_by = None
        blocking = work_breakdown.get("main_thread_blocking", {})
        if blocking:
            blocking_normalized = {
                key.replace("_ms", ""): value for key, value in blocking.items()
            }
            main_thread_blocked_by, blocked_reason = _dominant_category(blocking_normalized)
            if blocked_reason:
                _set_assumption(assumptions, "main_thread_blocked_by", blocked_reason)
        else:
            _set_assumption(
                assumptions,
                "main_thread_blocked_by",
                "No main thread blocking breakdown available"
            )

        result = {
            "schema_version": schema_version,
            "focus_process": focus_process,
            "focus_pid": focus_pid,
            "trace_path": trace_path,
            "trace_duration_ms": trace_duration_ms,
            "processes": processes,
            "startup_ms": startup_ms,
            "threads": {
                "main_thread": main_thread,
                "top_threads_by_slice_ms": cpu_features.get("top_threads_by_slice_ms", [])
            },
            "ui_thread_long_tasks": {
                "threshold_ms": long_task_ms,
                "count": long_task_count,
                "top": long_task_top
            },
            "features": {
                "long_slices_attributed": long_slices_attributed,
                "app_sections": app_sections,
                "frame_features": frame_features,
                "cpu_features": cpu_features,
                "work_breakdown": work_breakdown
            },
            "summary": {
                "main_thread_found": main_thread is not None,
                "top_app_sections": top_app_sections,
                "top_long_slice_name": top_long_slice_name,
                "dominant_work_category": dominant_work_category,
                "main_thread_blocked_by": main_thread_blocked_by
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
        _set_assumption(
            result["assumptions"],
            "frames",
            "Frame features computed from doFrame slices (p95/jank best-effort)"
        )
        _set_assumption(
            result["assumptions"],
            "classification",
            "Classification is pid+name token based and best-effort; unknown used when uncertain."
        )
        if focus_process and focus_pid is None:
            _set_assumption(
                result["assumptions"],
                "focus_process",
                f"No matching process found for focus_process={focus_process}"
            )
        if focus_pid is None:
            _set_assumption(
                result["assumptions"],
                "main_thread",
                "Main thread not resolved because focus_pid is null"
            )
        elif main_thread is None:
            _set_assumption(
                result["assumptions"],
                "main_thread",
                f"Main thread not found for pid={focus_pid}"
            )
        return result
    finally:
        analyzer.close()
