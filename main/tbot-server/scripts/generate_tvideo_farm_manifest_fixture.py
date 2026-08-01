#!/usr/bin/env python3
"""Run the backend fixture builder without depending on Vite/Rollup native addons."""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--node", type=Path, default=Path(os.environ.get("NODE", "node")))
    args = parser.parse_args()
    backend = args.backend_root.resolve(strict=True)
    source = Path(__file__).with_suffix(".ts").resolve(strict=True)
    tsc = (backend / "node_modules" / "typescript" / "lib" / "typescript.js").resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="tvideo-farm-manifest-") as directory:
        compiled = Path(directory) / "generator.mjs"
        transpile = (
            "const fs=require('fs'),ts=require(process.argv[1]);"
            "const src=fs.readFileSync(process.argv[2],'utf8');"
            "const out=ts.transpileModule(src,{compilerOptions:{module:ts.ModuleKind.ESNext,"
            "target:ts.ScriptTarget.ES2022}}).outputText;"
            "fs.writeFileSync(process.argv[3],out);"
        )
        subprocess.run(
            [str(args.node), "-e", transpile, str(tsc), str(source), str(compiled)],
            check=True,
        )
        subprocess.run(
            [
                str(args.node),
                str(compiled),
                "--backend-root",
                str(backend),
                "--output",
                str(args.output.resolve()),
            ],
            check=True,
        )


if __name__ == "__main__":
    main()
