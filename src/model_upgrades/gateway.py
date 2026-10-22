"""Send test requests through Unity Gateway."""

import logging
import uuid

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import BadRequest, NotFound, PermissionDenied
from databricks.sdk.service.catalog import (
    ModelService,
    ModelServiceConfig,
    ModelServiceConfigDestinationConfig,
    ModelServiceConfigDestinationConfigDestinationType,
    ModelServiceConfigPayPerTokenConfig,
    ModelServiceConfigRoutingConfig,
)

logger = logging.getLogger(__name__)

# A fresh name per probe matters: the gateway briefly caches routing by service name, so reusing one would send
# the request to whichever model the previous probe used.
PROBE_PREFIX = "zz_upgrade_probe_"


def query(w: WorkspaceClient, full_name: str, supported_api_types: list[str] | None):
    """Send a 16-token request to the model service catalog.schema.name. Raises if it doesn't answer."""
    if "mlflow/v1/chat/completions" in (supported_api_types or []):
        path = "/ai-gateway/mlflow/v1/chat/completions"
        body = {"messages": [{"role": "user", "content": "Reply with OK."}], "max_tokens": 16}
    else:
        path = "/ai-gateway/mlflow/v1/responses"
        body = {"input": "Reply with OK.", "max_output_tokens": 16}
    w.api_client.do("POST", path, body={"model": full_name, **body})


def probe(w: WorkspaceClient, schema: str, model: str) -> str | None:
    """Check that a system.ai model answers, via a throwaway model service in schema (catalog.schema).

    system.ai lists models that aren't served in every region, and the gateway only rejects them at request time.
    Returns the error if the model doesn't answer, or None if it does.
    """
    service_id = f"{PROBE_PREFIX}{uuid.uuid4().hex[:8]}"
    full_name = f"{schema}.{service_id}"
    # Failing to create the probe (e.g. missing CREATE_SERVICE) is our problem, not the model's, so let it raise.
    service = w.ai_gateway.create_model_service(
        model_service=ModelService(
            comment=f"Temporary availability probe for {model}. Safe to delete.",
            config=ModelServiceConfig(
                routing=ModelServiceConfigRoutingConfig(
                    destinations=[
                        ModelServiceConfigDestinationConfig(
                            name="primary",
                            destination_type=ModelServiceConfigDestinationConfigDestinationType.DESTINATION_TYPE_PAY_PER_TOKEN_FOUNDATION_MODEL,
                            pay_per_token_config=ModelServiceConfigPayPerTokenConfig(model=f"models/system.ai.{model}"),
                        )
                    ]
                )
            ),
        ),
        parent=f"schemas/{schema}",
        model_service_id=service_id,
    )
    try:
        query(w, full_name, service.supported_api_types)
    except (BadRequest, NotFound, PermissionDenied) as e:
        return str(e)
    finally:
        _delete_if_exists(w, full_name)
    return None


def delete_stale_probes(w: WorkspaceClient, schema: str):
    """Remove probe services left behind by a run that died mid-probe."""
    for service in w.ai_gateway.list_model_services(parent=f"schemas/{schema}"):
        full_name = service.name.removeprefix("model-services/")
        if full_name.removeprefix(f"{schema}.").startswith(PROBE_PREFIX):
            logger.info("Deleting leftover probe service %s", full_name)
            _delete_if_exists(w, full_name)


def _delete_if_exists(w: WorkspaceClient, full_name: str):
    try:
        w.ai_gateway.delete_model_service(f"model-services/{full_name}")
    except NotFound:
        pass
