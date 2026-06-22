from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path
from shutil import which

from sliderefine.server.local import LocalEditorServer, LocalEditorSession


ROOT = Path(__file__).resolve().parents[1]


def npm_command() -> str:
    return which("npm") or which("npm.cmd") or "npm"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("--evidence", default="evidence/working")
    args = parser.parse_args()
    evidence = (ROOT / args.evidence).resolve()
    with tempfile.TemporaryDirectory(prefix="sliderefine-browser-e2e-") as tmp:
        session = LocalEditorSession.from_input(Path(args.input), Path(tmp))
        server = LocalEditorServer(session).start()
        try:
            env = {
                **os.environ,
                "SLIDEREFINE_E2E_URL": server.url,
                "SLIDEREFINE_EVIDENCE_DIR": str(evidence),
            }
            completed = subprocess.run(
                [npm_command(), "run", "test:e2e"],
                cwd=ROOT / "apps" / "web",
                text=True,
                capture_output=True,
                env=env,
            )
            report = {
                "status": "ok" if completed.returncode == 0 else "fail",
                "serverUrl": server.url,
                "documentUri": session.document_path.as_uri(),
                "exitCode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
            output = evidence / "browser" / "local-service-report.json"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            return completed.returncode
        finally:
            server.stop()


if __name__ == "__main__":
    raise SystemExit(main())
