"""Run the evaluation suite against the real model and write a report.

    python scripts/evaluate.py                         # all questions; writes docs/evaluation_report.md
    python scripts/evaluate.py --only filtering,unsafe\\ SQL
    python scripts/evaluate.py --list                  # show the questions without running anything

It calls the model named in backend/.env (a few minutes for the full set, and it uses your model quota).
The data it works on is a private copy in a temporary folder.
"""

import argparse
import sys
import tempfile
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import Settings  # noqa: E402
from evaluation import metrics, report, runner  # noqa: E402
from evaluation.cases import CASES  # noqa: E402

DOCS = BACKEND.parent / "docs"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Evaluate the text-to-SQL assistant.")
    p.add_argument("--only", help="comma-separated categories to run (see --list)")
    p.add_argument("--workers", type=int, default=3, help="conversations to run at the same time (default 3)")
    p.add_argument("--report", default=str(DOCS / "evaluation_report.md"), help="where to write the Markdown report")
    p.add_argument("--json", default=str(DOCS / "evaluation_results.json"), help="where to write the full results")
    p.add_argument("--list", action="store_true", help="list the questions and exit")
    return p


def _load_model():
    from app.services.llm_service import AzureLLM

    settings = Settings(_env_file=".env")
    if not (settings.genai_platform_api_key and settings.azure_openai_endpoint and settings.vision_deployment_name):
        return None, settings
    return AzureLLM(settings), settings


def main(argv=None, llm=None) -> int:
    args = _parser().parse_args(argv)
    categories = list(dict.fromkeys(c.category for c in CASES))

    if args.list:
        for c in CASES:
            print(f"{c.id:30s} {c.category:20s} {c.expect:8s} {c.question}")
        print(f"\n{len(CASES)} questions in {len(categories)} categories.")
        return 0

    chosen = CASES
    if args.only:
        wanted = [name.strip() for name in args.only.split(",") if name.strip()]
        unknown = [name for name in wanted if name not in categories]
        if unknown:
            print(f"Unknown category '{unknown[0]}'. Choose from: {', '.join(categories)}.", file=sys.stderr)
            return 2
        chosen = tuple(c for c in CASES if c.category in wanted)

    model_name = "injected"
    settings = Settings(_env_file=".env")
    if llm is None:
        llm, settings = _load_model()
        if llm is None:
            print("The model is not configured. Set GENAI_PLATFORM_API_KEY, AZURE_OPENAI_ENDPOINT and "
                  "VISION_DEPLOYMENT_NAME in backend/.env.", file=sys.stderr)
            return 2
    model_name = settings.vision_deployment_name or model_name

    done = 0

    def show(result):
        nonlocal done
        done += 1
        mark = "pass" if result.passed else "FAIL"
        print(f"[{done}/{len(chosen)}] {mark}  {result.id}  ({result.total_ms / 1000:.1f}s)", flush=True)

    with tempfile.TemporaryDirectory(prefix="evaluation-") as folder:
        env = runner.build_environment(Path(folder))
        try:
            results = runner.run_all(llm, env, chosen, workers=args.workers, progress=show)
        finally:
            env.close()

    summary = metrics.summarise(results)
    meta = {
        "date": date.today().isoformat(), "model": model_name, "workers": args.workers,
        "settings": {
            "ambiguity_check": settings.ambiguity_check, "ambiguity_parallel": settings.ambiguity_parallel,
            "sql_max_attempts": settings.sql_max_attempts, "query_timeout_seconds": settings.query_timeout_seconds,
        },
    }
    for path, text in ((args.report, report.render_markdown(summary, results, meta)),
                       (args.json, report.to_json(summary, results, meta))):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text, encoding="utf-8")

    print(f"\n{summary['passed']} of {summary['cases']} passed. Report: {args.report}")
    unsafe = summary["unsafe"]
    if unsafe["data_unchanged"] not in (None, 1.0) or unsafe["no_leak"] not in (None, 1.0):
        print("SAFETY FAILURE: a request changed data or revealed blocked values. See the report.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
