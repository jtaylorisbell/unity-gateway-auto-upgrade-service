"""check-models: bump the system.ai model pinned in each bundle model service to the newest servable version.

Edits the resources file in place and writes a Markdown summary for the pull request body. Sets changed=true|false
in $GITHUB_OUTPUT when run under GitHub Actions.
"""

import argparse
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from databricks.sdk import WorkspaceClient

from model_upgrades.gateway import delete_stale_probes, probe
from model_upgrades.versions import newer_models

logger = logging.getLogger(__name__)

SYSTEM_AI_PREFIX = "models/system.ai."
PAY_PER_TOKEN = "DESTINATION_TYPE_PAY_PER_TOKEN_FOUNDATION_MODEL"


@dataclass
class Report:
    upgrades: dict[str, tuple[str, str]] = field(default_factory=dict)  # service key -> (current, proposed)
    unservable: dict[str, str] = field(default_factory=dict)  # model -> error
    untracked: dict[str, str] = field(default_factory=dict)  # service key -> reason


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--resources", type=Path, default=Path("resources/model_services.yml"))
    # Defaults to the schema the bundle deploys to, which these same variables set.
    catalog, schema = os.environ.get("BUNDLE_VAR_catalog"), os.environ.get("BUNDLE_VAR_schema")
    bundle_schema = f"{catalog}.{schema}" if catalog and schema else None
    parser.add_argument(
        "--probe-schema",
        default=bundle_schema,
        required=bundle_schema is None,
        help="catalog.schema where throwaway probe services can be created. Defaults to the bundle's schema.",
    )
    parser.add_argument("--summary-file", type=Path, help="Where to write the Markdown summary.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(levelname)s %(message)s")

    w = WorkspaceClient()
    text = args.resources.read_text()
    report = check(w, yaml.safe_load(text)["resources"]["model_services"], args.probe_schema)

    if report.upgrades:
        args.resources.write_text(apply_upgrades(text, report.upgrades))
    summary = render(report)
    print(summary)
    if args.summary_file:
        args.summary_file.write_text(summary)
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"changed={'true' if report.upgrades else 'false'}\n")


def check(w: WorkspaceClient, services: dict, probe_schema: str) -> Report:
    available = [m.name for m in w.registered_models.list(catalog_name="system", schema_name="ai")]
    logger.info("Found %d models in system.ai", len(available))
    delete_stale_probes(w, probe_schema)

    report = Report()
    servable: dict[str, bool] = {}
    for key, service in services.items():
        current = pinned_model(service)
        if current is None:
            report.untracked[key] = "doesn't route to exactly one pay-per-token system.ai model"
            continue
        candidates = newer_models(current, available)
        if candidates is None:
            report.untracked[key] = f"can't tell which part of `{current}` is the version"
            continue
        for model in candidates:
            if model not in servable:
                error = probe(w, probe_schema, model)
                servable[model] = error is None
                if error:
                    logger.warning("%s is not servable: %s", model, error)
                    report.unservable[model] = error
            if servable[model]:
                report.upgrades[key] = (current, model)
                break
        logger.info("%s: %s -> %s", key, current, report.upgrades.get(key, (None, "no change"))[1])
    return report


def pinned_model(service: dict) -> str | None:
    """The system.ai model a bundle model service routes to, if it routes to exactly one pay-per-token model."""
    destinations = ((service.get("config") or {}).get("routing") or {}).get("destinations") or []
    if len(destinations) != 1 or destinations[0].get("destination_type") != PAY_PER_TOKEN:
        return None
    model = (destinations[0].get("pay_per_token_config") or {}).get("model") or ""
    if not model.startswith(SYSTEM_AI_PREFIX) or "${" in model:
        return None
    return model.removeprefix(SYSTEM_AI_PREFIX)


def apply_upgrades(text: str, upgrades: dict[str, tuple[str, str]]) -> str:
    """Swap model names in the YAML text so formatting and comments are untouched."""
    for current, proposed in set(upgrades.values()):
        # The lookahead keeps databricks-claude-opus-5 from matching inside databricks-claude-opus-5-5.
        text = re.sub(rf"(?<=models/system\.ai\.){re.escape(current)}(?![\w-])", proposed, text)
    return text


def render(report: Report) -> str:
    lines = []
    if report.upgrades:
        lines += [
            "Newer system.ai models are available for these Unity Gateway model services.",
            "",
            "| Model service | Current | Proposed |",
            "|---|---|---|",
            *(f"| `{key}` | `{current}` | `{new}` |" for key, (current, new) in report.upgrades.items()),
            "",
            (
                "Each proposed model answered a test request through Unity Gateway before this PR was opened. "
                "CI deploys this branch to staging and tests every service. Merging deploys to prod."
            ),
        ]
    else:
        lines.append("All model services are on the newest servable system.ai model.")
    if report.unservable:
        lines += ["", "Skipped because they don't answer in this workspace yet:", ""]
        lines += [f"- `{model}`: {error}" for model, error in report.unservable.items()]
    if report.untracked:
        lines += ["", "Not tracked:", ""]
        lines += [f"- `{key}`: {reason}" for key, reason in report.untracked.items()]
    return "\n".join(lines) + "\n"
