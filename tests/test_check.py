import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from databricks.sdk.errors import NotFound
from databricks.sdk.service.catalog import ModelService

from model_upgrades import check, smoke_test
from model_upgrades.gateway import PROBE_PREFIX

RESOURCES = Path(__file__).parents[1] / "resources" / "model_services.yml"

SYSTEM_AI = [
    "databricks-claude-opus-5",
    "databricks-claude-opus-5-5",
    "databricks-claude-fable-5",
    "databricks-claude-fable-5-1",
    "databricks-gpt-6-luna",
    "databricks-gpt-6-1-luna",
]


class FakeWorkspace:
    """Just enough of WorkspaceClient for probes: system.ai listing, model service CRUD, and gateway queries."""

    def __init__(self, unservable=(), services=()):
        self.unservable = set(unservable)
        self.services = {f"model-services/{name}": model for name, model in services}
        self.probed: list[str] = []
        self.registered_models = SimpleNamespace(
            list=lambda catalog_name, schema_name: [SimpleNamespace(name=m) for m in SYSTEM_AI]
        )
        self.ai_gateway = SimpleNamespace(
            create_model_service=self._create,
            get_model_service=self._get,
            delete_model_service=self._delete,
            list_model_services=lambda parent: [ModelService(name=n) for n in self.services],
        )
        self.api_client = SimpleNamespace(do=self._query)

    def _create(self, model_service, parent, model_service_id):
        model = model_service.config.routing.destinations[0].pay_per_token_config.model
        self.services[f"model-services/{parent.removeprefix('schemas/')}.{model_service_id}"] = model
        return ModelService(supported_api_types=["mlflow/v1/chat/completions"])

    def _get(self, name):
        if name not in self.services:
            raise NotFound(name)
        return ModelService(name=name, supported_api_types=["mlflow/v1/chat/completions"])

    def _delete(self, name):
        if name not in self.services:
            raise NotFound(name)
        del self.services[name]

    def _query(self, method, path, body):
        model = self.services[f"model-services/{body['model']}"].removeprefix("models/system.ai.")
        self.probed.append(model)
        if model in self.unservable:
            raise NotFound(f"Model {model} is not available in your region.")


def service(model, destination_type=check.PAY_PER_TOKEN, extra_destinations=()):
    destination = {"destination_type": destination_type, "pay_per_token_config": {"model": model}}
    return {"config": {"routing": {"destinations": [destination, *extra_destinations]}}}


def test_resources_file_pins_trackable_models():
    services = yaml.safe_load(RESOURCES.read_text())["resources"]["model_services"]
    assert services
    for key, svc in services.items():
        assert svc["model_service_id"] == key
        assert check.pinned_model(svc) is not None, key


@pytest.mark.parametrize(
    "svc",
    [
        service("models/system.ai.x-1", extra_destinations=[{}]),
        service("models/system.ai.x-1", destination_type="DESTINATION_TYPE_EXTERNAL_FOUNDATION_MODEL"),
        service("models/main.custom.x-1"),
        service("models/system.ai.${var.opus_model}"),
    ],
    ids=["traffic-split", "external", "not-system-ai", "interpolated"],
)
def test_pinned_model_ignores_services_it_cannot_track(svc):
    assert check.pinned_model(svc) is None


def test_upgrades_to_newest_servable_and_skips_unservable():
    w = FakeWorkspace(unservable={"databricks-claude-fable-5-1"}, services=[(f"cat.sch.{PROBE_PREFIX}dead", "m")])
    report = check.check(
        w,
        {
            "opus": service("models/system.ai.databricks-claude-opus-5"),
            "fable": service("models/system.ai.databricks-claude-fable-5"),
            "luna": service("models/system.ai.databricks-gpt-6-1-luna"),
            "inkling": service("models/system.ai.databricks-inkling"),
        },
        "cat.sch",
    )

    assert report.upgrades == {"opus": ("databricks-claude-opus-5", "databricks-claude-opus-5-5")}
    assert "not available in your region" in report.unservable["databricks-claude-fable-5-1"]
    assert "inkling" in report.untracked
    assert w.services == {}, "probe services, including leftovers, must be deleted"


def test_each_model_is_probed_once():
    w = FakeWorkspace()
    check.check(
        w,
        {k: service("models/system.ai.databricks-claude-opus-5") for k in ("a", "b")},
        "cat.sch",
    )
    assert w.probed == ["databricks-claude-opus-5-5"]


def test_apply_upgrades_only_touches_whole_model_names():
    text = (
        "a: models/system.ai.databricks-claude-opus-5\n"
        "b: models/system.ai.databricks-claude-opus-5-5\n"
        "c: models/system.ai.databricks-claude-opus-5 # trailing comment\n"
    )
    upgrades = {"a": ("databricks-claude-opus-5", "databricks-claude-opus-6")}
    assert check.apply_upgrades(text, upgrades) == (
        "a: models/system.ai.databricks-claude-opus-6\n"
        "b: models/system.ai.databricks-claude-opus-5-5\n"
        "c: models/system.ai.databricks-claude-opus-6 # trailing comment\n"
    )


def test_main_rewrites_resources_and_reports_to_github(tmp_path, monkeypatch):
    resources = tmp_path / "model_services.yml"
    original = RESOURCES.read_text()
    resources.write_text(original.replace("databricks-claude-opus-5-5", "databricks-claude-opus-5"))
    output, summary = tmp_path / "github_output", tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    # The probe schema defaults to the target's, as in CI.
    targets = []
    monkeypatch.setattr(check, "bundle_schema", lambda target: targets.append(target) or "cat.sch")
    monkeypatch.setattr(check, "WorkspaceClient", lambda: FakeWorkspace(unservable={"databricks-gpt-6-1-luna"}))
    monkeypatch.setattr(
        sys, "argv", ["check-models", "-t", "staging", f"--resources={resources}", f"--summary-file={summary}"]
    )

    check.main()

    assert targets == ["staging"]
    assert resources.read_text() == original
    assert output.read_text() == "changed=true\n"
    assert "| `opus` | `databricks-claude-opus-5` | `databricks-claude-opus-5-5` |" in summary.read_text()
    assert "- `databricks-gpt-6-1-luna`\n" in summary.read_text()
    assert "not available in your region" not in summary.read_text()


def test_bundle_schema_reads_the_targets_variables(monkeypatch):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        variables = {"catalog": {"value": "cat"}, "schema": {"value": "sch_staging"}}
        return SimpleNamespace(stdout=json.dumps({"variables": variables}))

    monkeypatch.setattr(check.subprocess, "run", run)

    assert check.bundle_schema("staging") == "cat.sch_staging"
    assert check.bundle_schema(None) == "cat.sch_staging"
    assert commands == [
        ["databricks", "bundle", "validate", "-o", "json", "-t", "staging"],
        ["databricks", "bundle", "validate", "-o", "json"],
    ]


def test_smoke_test_reports_services_that_do_not_answer(tmp_path, monkeypatch):
    w = FakeWorkspace(
        unservable={"databricks-claude-fable-5-1"},
        services=[("cat.sch.opus", "models/system.ai.databricks-claude-opus-5-5")],
    )
    w.services["model-services/cat.sch.fable"] = "models/system.ai.databricks-claude-fable-5-1"
    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps(
            {"resources": {"model_services": {"opus": {"id": "cat.sch.opus"}, "fable": {"id": "cat.sch.fable"}}}}
        )
    )
    monkeypatch.setattr(smoke_test, "WorkspaceClient", lambda: w)
    monkeypatch.setattr(sys, "argv", ["smoke-test", str(summary)])

    with pytest.raises(SystemExit, match="1 of 2 model services failed:\ncat.sch.fable"):
        smoke_test.main()
