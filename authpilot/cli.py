import argparse
import logging

import uvicorn

from authpilot.agent.planners import ScriptedPlanner, build_planner
from authpilot.config import get_settings
from authpilot.evaluation import run_eval, save_report


def main() -> None:
    parser = argparse.ArgumentParser(prog="authpilot", description="AuthPilot")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Run the web app")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    ev = sub.add_parser("eval", help="Score a planner on the labelled synthetic cases")
    ev.add_argument("--planner", choices=["auto", "offline", "claude"], default="offline")
    ev.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    if args.command == "serve":
        uvicorn.run("authpilot.main:app", host=args.host, port=args.port)
        return

    settings = get_settings()
    if args.planner == "offline":
        planner = ScriptedPlanner()
    else:
        if args.planner == "claude" and not settings.anthropic_api_key:
            raise SystemExit("ANTHROPIC_API_KEY is required for --planner claude")
        planner = build_planner(settings.anthropic_api_key, settings.claude_model)

    report = run_eval(planner, settings, args.limit)
    path = save_report(report)
    print(f"\nPlanner: {report['planner']}   ({report['seconds']}s)")
    for row in report["cases"]:
        mark = "OK " if row["correct"] else "XX "
        trap = " [trap]" if row["trap"] else ""
        print(f"  {mark}{row['patient_id']:<12} expected={row['expected']:<13} "
              f"got={row['got']:<13} {row['scenario']}{trap}")  # fmt: skip
    o, t, n = report["overall"], report["traps"], report["non_traps"]
    print(f"\nOverall {o['correct']}/{o['total']} ({o['accuracy']:.0%})  |  "
          f"traps {t['correct']}/{t['total']}  |  others {n['correct']}/{n['total']}")  # fmt: skip
    print(f"Report: {path}")


if __name__ == "__main__":
    main()
