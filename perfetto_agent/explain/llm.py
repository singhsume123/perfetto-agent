"""LLM explanation layer: input shaping, OpenAI call, validation, rendering."""

from __future__ import annotations

import json
import os
import time
import sys
import requests
import urllib.error
from typing import Any


DEFAULT_MODEL = "gpt-4o"
LLM_TIMEOUT_SECONDS = 30
LLM_MAX_RETRIES = 4


def _trim_list(value: Any, limit: int) -> list:
    if not isinstance(value, list):
        return []
    return value[:limit]


def build_llm_input(analysis: dict, baseline: dict | None = None) -> dict:
    def extract(source: dict) -> dict:
        features = source.get("features", {}) if isinstance(source, dict) else {}
        return {
            "summary": source.get("summary", {}) if isinstance(source, dict) else {},
            "features": {
                "time_windows": features.get("time_windows"),
                "window_breakdown": features.get("window_breakdown"),
                "work_breakdown": features.get("work_breakdown"),
                "suspects": _trim_list(features.get("suspects"), 5),
                "long_slices_attributed": {
                    "top": _trim_list(
                        (features.get("long_slices_attributed") or {}).get("top"),
                        10
                    )
                },
                "app_sections": {
                    "top_by_total_ms": _trim_list(
                        (features.get("app_sections") or {}).get("top_by_total_ms"),
                        5
                    )
                }
            },
            "assumptions": source.get("assumptions", {}) if isinstance(source, dict) else {}
        }

    payload = {
        "current": extract(analysis)
    }

    if baseline is not None:
        payload["baseline"] = extract(baseline)
        payload["deltas"] = compute_deltas(payload["current"], payload["baseline"])

    return payload


def compute_deltas(current: dict, baseline: dict) -> dict:
    deltas: dict[str, Any] = {}
    current_summary = current.get("summary", {})
    baseline_summary = baseline.get("summary", {})
    deltas["summary_changes"] = {
        "startup_dominant_category": {
            "baseline": baseline_summary.get("startup_dominant_category"),
            "current": current_summary.get("startup_dominant_category")
        },
        "steady_state_dominant_category": {
            "baseline": baseline_summary.get("steady_state_dominant_category"),
            "current": current_summary.get("steady_state_dominant_category")
        },
        "top_suspect": {
            "baseline": baseline_summary.get("top_suspect"),
            "current": current_summary.get("top_suspect")
        }
    }

    def extract_by_category(window_data: dict) -> dict:
        return window_data.get("by_category_ms") or {}

    current_windows = (current.get("features") or {}).get("window_breakdown") or {}
    baseline_windows = (baseline.get("features") or {}).get("window_breakdown") or {}
    window_deltas = {}
    for window_name in ["startup", "steady_state"]:
        current_totals = extract_by_category(current_windows.get(window_name, {}))
        baseline_totals = extract_by_category(baseline_windows.get(window_name, {}))
        delta_entry = {}
        for category in ["app", "framework", "system", "unknown"]:
            delta_entry[category] = float(current_totals.get(category, 0.0) or 0.0) - float(
                baseline_totals.get(category, 0.0) or 0.0
            )
        window_deltas[window_name] = delta_entry
    deltas["window_category_deltas_ms"] = window_deltas
    return deltas


def _system_prompt() -> str:
    return (
        "You are a performance narrator. "
        "Use only the provided JSON input. "
        "Every claim must include evidence paths. "
        "If evidence is missing, say 'insufficient evidence' and list missing fields. "
        "No fixes or optimizations; only next inspection steps. "
        "Keep output concise and technical."
    )


def _user_prompt(llm_input: dict) -> str:
    return (
        "Return JSON with keys: title, high_level, key_findings, suspects, "
        "next_steps, limitations. Each list item must include text and evidence "
        "(list of JSON paths). Use only the provided JSON input:\n"
        f"{json.dumps(llm_input, indent=2)}"
    )


def call_openai(llm_input: dict) -> dict:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY; set it to use the LLM explanation.")
    model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": _user_prompt(llm_input)}
        ],
        "temperature": 0.0
    }

    data = json.dumps(payload).encode("utf-8")
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    last_error = None
    for attempt in range(LLM_MAX_RETRIES):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=LLM_TIMEOUT_SECONDS)
            print(f"STATUS: {resp.status_code}", file=sys.stderr)
            print(f"CONTENT-TYPE: {resp.headers.get('content-type')}", file=sys.stderr)
            print(f"BODY (first 500): {resp.text[:500]}", file=sys.stderr)
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        sleep_seconds = float(retry_after)
                    except ValueError:
                        sleep_seconds = 1.0 + (2 ** attempt)
                else:
                    sleep_seconds = 1.0 + (2 ** attempt)
                time.sleep(sleep_seconds)
                continue
            resp.raise_for_status()
            parsed = resp.json()
            content = parsed["choices"][0]["message"]["content"]
            try:
                return json.loads(content)
            except json.JSONDecodeError as exc:
                cleaned = content.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.lstrip("`")
                    cleaned = cleaned.replace("json", "", 1).strip()
                    if cleaned.endswith("```"):
                        cleaned = cleaned[:-3].strip()
                try:
                    return json.loads(cleaned)
                except json.JSONDecodeError:
                    raise RuntimeError(f"LLM returned non-JSON content: {content[:500]}") from exc
        except Exception as exc:
            last_error = exc
            time.sleep(1.0 + attempt)

    raise RuntimeError(f"LLM request failed: {str(last_error)}")


def validate_llm_output(output: dict) -> list[str]:
    errors: list[str] = []
    required_keys = [
        "title",
        "high_level",
        "key_findings",
        "suspects",
        "next_steps",
        "limitations"
    ]
    for key in required_keys:
        if key not in output:
            errors.append(f"missing key: {key}")

    def validate_list(name: str):
        value = output.get(name)
        if not isinstance(value, list):
            errors.append(f"{name} is not a list")
            return
        for idx, item in enumerate(value):
            if not isinstance(item, dict):
                errors.append(f"{name}[{idx}] is not an object")
                continue
            if not item.get("text"):
                errors.append(f"{name}[{idx}].text missing")
            evidence = item.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                errors.append(f"{name}[{idx}].evidence missing")

    for list_key in ["key_findings", "suspects", "next_steps", "limitations"]:
        validate_list(list_key)

    return errors


def render_markdown(output: dict) -> str:
    lines = []
    lines.append(f"# {str(output.get('title', 'Performance Summary'))}")
    lines.append("")
    lines.append(str(output.get("high_level", "")))
    lines.append("")

    def render_section(title: str, key: str):
        lines.append(f"## {title}")
        items = output.get(key, [])
        for item in items:
            lines.append(f"- {str(item.get('text', ''))}")
        lines.append("")

    render_section("Key Findings", "key_findings")
    render_section("Suspects", "suspects")
    render_section("Next Steps", "next_steps")
    render_section("Limitations", "limitations")

    lines.append("## Evidence Appendix")
    for key in ["key_findings", "suspects", "next_steps", "limitations"]:
        lines.append(f"### {key}")
        for item in output.get(key, []):
            evidence = item.get("evidence", [])
            if evidence:
                lines.append(f"- {item.get('text', '')}")
                for path in evidence:
                    lines.append(f"  - {path}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def run_explain(analysis: dict, baseline: dict | None = None) -> tuple[dict, dict, str]:
    llm_input = build_llm_input(analysis, baseline)
    llm_output = call_openai(llm_input)
    errors = validate_llm_output(llm_output)
    if errors:
        raise RuntimeError(f"LLM output validation failed: {', '.join(errors)}")
    markdown = render_markdown(llm_output)
    return llm_input, llm_output, markdown
