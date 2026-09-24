from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import Settings
from .ingest import write_manifest
from .service import InvestigationService


def main() -> None:
    parser = argparse.ArgumentParser(description="HHGOA fraud investigation runner")
    parser.add_argument("command", choices=["validate", "investigate", "benchmark", "evaluate", "prepare"])
    parser.add_argument("case_id", nargs="?")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    configured = Settings(data_dir=args.data_dir) if args.data_dir else Settings()
    if args.command == "prepare":
        from .prepare import prepare_dataset

        print(prepare_dataset(configured.data_dir, args.output or configured.data_dir / "runtime" / "normalized"))
        return
    if args.command == "evaluate":
        from .evaluation import evaluate_history

        output = args.output or configured.data_dir / "artifacts" / "calibration"
        report = evaluate_history(configured.data_dir, output)
        print(json.dumps({"report": str(output / "report.json"), "holdout": report["holdout"]}, indent=2))
        return
    if args.command == "validate":
        destination = write_manifest(configured.data_dir, args.output)
        print(destination)
        return
    service = InvestigationService(configured)
    if args.command == "investigate":
        if not args.case_id:
            parser.error("investigate requires CASE_ID")
        result = service.investigate(args.case_id)
        print(json.dumps(result.answer.submission_dict(), indent=2))
    else:
        output = args.output or Path("cases")
        count = service.export_answers(output)
        print(f"wrote {count} answer files to {output}")


if __name__ == "__main__":
    main()
