from __future__ import annotations

"""Explicit administrative installer for the supplied TigerGraph assets.

This command is separate from the investigation process. It creates/updates the
graph, installs approved queries, and loads the immutable normalized package; it
does not drop graphs or delete data.
"""

import argparse
import json
import os
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Install and load HHGOA TigerGraph assets")
    parser.add_argument("--data-dir", type=Path, default=Path(os.getenv("HHGOA_DATA_DIR", Path.cwd())))
    parser.add_argument("--normalized", type=Path, default=None)
    parser.add_argument("--install", action="store_true", help="create/update schema, vector attribute, loaders, and queries")
    parser.add_argument("--load", action="store_true", help="upload and run every normalized loading job")
    args = parser.parse_args()
    if not args.install and not args.load:
        parser.error("choose --install, --load, or both")

    host = os.environ.get("TG_HOST", "")
    token = os.environ.get("TG_API_TOKEN", "")
    secret = os.environ.get("TG_SECRET", "")
    graph_name = os.environ.get("TG_GRAPHNAME", "FraudGraph")
    if not host or not (token or secret):
        parser.error("TG_HOST and either TG_API_TOKEN or TG_SECRET must be set in the process environment")

    from pyTigerGraph import TigerGraphConnection

    connection = TigerGraphConnection(
        host=host, graphname=graph_name, apiToken=token, gsqlSecret=secret,
        tgCloud=os.environ.get("TG_TGCLOUD", "true").lower() == "true",
    )
    graph_dir = args.data_dir / "graph"
    if args.install:
        for name in ("schema.gsql", "vectors.gsql", "loading.gsql", "queries.gsql"):
            print(json.dumps({"stage": "gsql", "file": name}, sort_keys=True))
            raw = (graph_dir / name).read_text(encoding="utf-8")
            if name == "schema.gsql":
                for statement in _schema_statements(raw):
                    try:
                        result = connection.gsql(statement, graphname=graph_name)
                    except Exception as error:
                        message = str(error).lower()
                        if (
                            "already exists" not in message
                            and "used by another object" not in message
                            and "graph name conflicts" not in message
                        ):
                            raise
                        result = f"skipped existing object: {statement.split()[2]}"
                    print(json.dumps({"file": name, "statement": statement, "result": result}, default=str, sort_keys=True))
            else:
                # Savanna's TigerGraph 4.x statements endpoint uses newlines
                # as statement boundaries and rejects legacy semicolons.
                try:
                    result = connection.gsql(_savanna_gsql(raw), graphname=graph_name)
                except Exception as error:
                    message = str(error).lower()
                    if not any(token in message for token in ("already exists", "conflict with another vector", "duplicate")):
                        raise
                    result = f"skipped existing objects: {error}"
                print(json.dumps({"file": name, "result": result}, default=str, sort_keys=True))

    if args.load:
        normalized = args.normalized or _latest_normalized(args.data_dir / "runtime" / "normalized")
        mapping = json.loads((graph_dir / "loading-map.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(prefix="hhgoa-tg-load-") as split_dir:
            for filename, config in mapping.items():
                path = normalized / filename
                if not path.exists():
                    raise FileNotFoundError(path)
                for part_number, part in enumerate(_load_parts(path, Path(split_dir)), start=1):
                    print(json.dumps({"stage": "load", "file": filename, "part": part_number, "job": config["job"]}, sort_keys=True))
                    result = connection.runLoadingJobWithFile(
                        str(part), config["file_tag"], config["job"],
                        timeout=900_000, sizeLimit=2_000_000_000,
                    )
                    print(json.dumps({"file": filename, "part": part_number, "job": config["job"], "result": result}, default=str, sort_keys=True))


def _latest_normalized(root: Path) -> Path:
    packages = sorted(path for path in root.iterdir() if path.is_dir())
    if not packages:
        raise FileNotFoundError(f"No normalized package under {root}; run `hhgoa prepare` first")
    return packages[-1]


def _savanna_gsql(text: str) -> str:
    """Normalize checked-in GSQL for Savanna's v4 statements endpoint."""
    lines: list[str] = []
    depth = 0
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if depth == 0:
            line = line.removesuffix(";")
        lines.append(line)
        depth += line.count("{") - line.count("}")
    return "\n".join(lines)


def _schema_statements(text: str) -> list[str]:
    return [
        line.rstrip().removesuffix(";")
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("//")
    ]


def _load_parts(path: Path, split_dir: Path, max_rows: int = 100_000) -> list[Path]:
    with path.open("r", encoding="utf-8", newline="") as source:
        header = source.readline()
        first_batch = []
        for _ in range(max_rows):
            line = source.readline()
            if not line:
                break
            first_batch.append(line)
        if len(first_batch) < max_rows and not source.readline():
            return [path]

    parts: list[Path] = []
    with path.open("r", encoding="utf-8", newline="") as source:
        source.readline()
        part_number = 0
        while True:
            batch = [source.readline() for _ in range(max_rows)]
            batch = [line for line in batch if line]
            if not batch:
                break
            part_number += 1
            part = split_dir / f"{path.stem}.part{part_number}{path.suffix}"
            part.write_text(header + "".join(batch), encoding="utf-8", newline="")
            parts.append(part)
    return parts


if __name__ == "__main__":
    main()
