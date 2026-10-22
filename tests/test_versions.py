import pytest

from model_upgrades.versions import family, newer_models, version

SYSTEM_AI = [
    "databricks-claude-3-7-sonnet",
    "databricks-claude-opus-4-8",
    "databricks-claude-opus-5",
    "databricks-claude-opus-5-5",
    "databricks-claude-sonnet-5-5",
    "databricks-gpt-5",
    "databricks-gpt-5-5",
    "databricks-gpt-5-5-pro",
    "databricks-gpt-5-4-mini",
    "databricks-gpt-5-1-codex-mini",
    "databricks-gpt-5-6-luna",
    "databricks-gpt-6-luna",
    "databricks-gpt-6-sol",
    "databricks-gpt-6-1-sol",
]


@pytest.mark.parametrize(
    "pinned, model, expected",
    [
        ("databricks-claude-opus-5", "databricks-claude-opus-5-5", (5, 5)),
        ("databricks-claude-opus-5", "databricks-claude-sonnet-5-5", None),
        ("databricks-gpt-5-6-luna", "databricks-gpt-6-luna", (6,)),
        ("databricks-gpt-5", "databricks-gpt-5-5", (5, 5)),
        ("databricks-gpt-5", "databricks-gpt-5-5-pro", None),
        ("databricks-gpt-5", "databricks-gpt-6-luna", None),
        ("databricks-gpt-5-4-mini", "databricks-gpt-5-1-codex-mini", None),
    ],
)
def test_family_matches_only_the_same_family(pinned, model, expected):
    assert version(family(pinned), model) == expected


@pytest.mark.parametrize("model", ["databricks-inkling", "databricks-deepseek-v4-1-flash-1017", "llama_v3_3_70b"])
def test_no_family_without_exactly_one_version(model):
    assert family(model) is None
    assert newer_models(model, SYSTEM_AI) is None


def test_newer_models_compares_numerically_newest_first():
    assert newer_models("databricks-claude-opus-4-8", SYSTEM_AI) == [
        "databricks-claude-opus-5-5",
        "databricks-claude-opus-5",
    ]
    assert newer_models("databricks-gpt-6-sol", SYSTEM_AI) == ["databricks-gpt-6-1-sol"]
    assert newer_models("databricks-claude-opus-5-5", SYSTEM_AI) == []
