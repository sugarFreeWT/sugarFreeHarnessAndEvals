from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from sugarfree.core.agent import Agent, AgentConfig
from sugarfree.core.events import EventSink
from sugarfree.evals.cases import EvalCase, load_suite
from sugarfree.evals.runner import EvalRunner
from sugarfree.evals.runtime import default_tools
from sugarfree.models.openai_compatible import OpenAICompatibleModel
from sugarfree.fs.workspace import WorkspaceFileStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a live OpenAI-compatible sugarfree eval suite.")
    parser.add_argument("suite", type=Path, help="Path to a suite with mode=live")
    parser.add_argument("--output-dir", type=Path, help="Directory for report and trajectories")
    parser.add_argument("--model", default=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"))
    parser.add_argument("--base-url", default=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
    parser.add_argument("--temperature", type=float, default=float(os.getenv("DEEPSEEK_TEMPERATURE", "0")))
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "60")),
    )
    parser.add_argument(
        "--model-timeout-seconds",
        type=float,
        default=_optional_float_env("DEEPSEEK_AGENT_MODEL_TIMEOUT_SECONDS"),
    )
    parser.add_argument(
        "--tool-timeout-seconds",
        type=float,
        default=_optional_float_env("DEEPSEEK_TOOL_TIMEOUT_SECONDS"),
    )
    return parser


async def run_cli(args: argparse.Namespace) -> int:
    suite = load_suite(args.suite)
    if suite.mode != "live":
        raise ValueError("the live runner requires a suite with mode=live")
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY is required and is read only from the environment")
    if not args.model:
        raise ValueError("model name cannot be empty")
    if args.temperature < 0:
        raise ValueError("temperature cannot be negative")
    if args.timeout_seconds <= 0:
        raise ValueError("timeout-seconds must be greater than zero")
    for name, value in (
        ("model-timeout-seconds", args.model_timeout_seconds),
        ("tool-timeout-seconds", args.tool_timeout_seconds),
    ):
        if value is not None and value <= 0:
            raise ValueError(f"{name} must be greater than zero")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_dir or Path("runs") / f"{suite.name}-{stamp}"
    model = OpenAICompatibleModel(
        api_key=api_key,
        model=args.model,
        base_url=args.base_url,
        temperature=args.temperature,
        timeout_seconds=args.timeout_seconds,
    )

    def agent_factory(
        case: EvalCase,
        sink: EventSink,
        workspace: WorkspaceFileStore | None,
    ) -> Agent:
        return Agent(
            model=model,
            tools=default_tools(workspace),
            config=AgentConfig(
                max_steps=case.agent_max_steps,
                model_timeout_seconds=args.model_timeout_seconds,
                tool_timeout_seconds=args.tool_timeout_seconds,
            ),
            event_sink=sink,
            workspace=workspace,
        )

    report = await EvalRunner(agent_factory=agent_factory).run(
        suite,
        output_dir,
        metadata={
            "provider": "openai-compatible",
            "model": args.model,
            "base_url": _safe_url(args.base_url),
            "temperature": args.temperature,
            "timeout_seconds": args.timeout_seconds,
            "model_timeout_seconds": args.model_timeout_seconds,
            "tool_timeout_seconds": args.tool_timeout_seconds,
            "suite_sha256": _sha256(args.suite),
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "api_key_recorded": False,
        },
    )
    print(f"Suite: {report.suite_name}")
    for case in report.cases:
        marker = "PASS" if case.passed else "FAIL"
        print(
            f"[{marker}] {case.case_id}: {case.result.termination_reason.value}, "
            f"steps={case.result.steps}, tools={case.result.tool_calls}"
        )
        for score in case.scores:
            if not score.passed:
                print(f"       {score.scorer}: {score.detail}")
    print(f"Result: {report.passed_cases}/{len(report.cases)} cases passed")
    print(f"Report: {output_dir / 'report.json'}")
    return 0 if report.passed else 1


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _optional_float_env(name: str) -> float | None:
    value = os.getenv(name)
    return None if value in {None, ""} else float(value)


def _safe_url(value: str) -> str:
    parsed = urlsplit(value)
    hostname = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port is not None else ""
    return urlunsplit((parsed.scheme, f"{hostname}{port}", parsed.path, "", ""))


def main() -> None:
    try:
        raise SystemExit(asyncio.run(run_cli(build_parser().parse_args())))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
