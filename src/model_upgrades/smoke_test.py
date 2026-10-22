"""smoke-test: send a tiny request through every model service in a deployed bundle.

Takes the output of `databricks bundle summary -o json`, so it tests exactly what that target deployed.
"""

import argparse
import json
import sys
from pathlib import Path

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import DatabricksError

from model_upgrades.gateway import query


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bundle_summary", type=Path, help="JSON from `databricks bundle summary -o json`.")
    args = parser.parse_args()

    services = json.loads(args.bundle_summary.read_text())["resources"].get("model_services", {})
    failures = smoke_test(WorkspaceClient(), [service["id"] for service in services.values()])
    if failures:
        sys.exit(f"{len(failures)} of {len(services)} model services failed:\n" + "\n".join(failures))


def smoke_test(w: WorkspaceClient, full_names: list[str]) -> list[str]:
    failures = []
    for full_name in full_names:
        try:
            service = w.ai_gateway.get_model_service(f"model-services/{full_name}")
            query(w, full_name, service.supported_api_types)
            print(f"ok    {full_name}")
        except DatabricksError as e:
            print(f"FAIL  {full_name}: {e}")
            failures.append(f"{full_name}: {e}")
    return failures
