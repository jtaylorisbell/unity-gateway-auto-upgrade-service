"""Find newer versions of a system.ai model.

A model's version is the one run of dash-separated numbers in its name, and its family is everything around it:
databricks-claude-opus-[5-5], databricks-gpt-[6-1]-sol, databricks-gpt-[5-4]-mini. Anthropic and OpenAI put the
version in different places, so reading it from the pinned name avoids keeping a list of model classes in code.
"""

import re
from itertools import groupby

Version = tuple[int, ...]


def family(model: str) -> re.Pattern | None:
    """Regex matching every version of model's family, capturing the version.

    Returns None when the name doesn't have exactly one run of numbers, because then the version is ambiguous
    (databricks-deepseek-v4-1-flash-1017) or missing (databricks-inkling).
    """
    runs = [(is_number, list(parts)) for is_number, parts in groupby(model.split("-"), key=str.isdigit)]
    numeric = [i for i, (is_number, _) in enumerate(runs) if is_number]
    if len(numeric) != 1:
        return None
    prefix = "".join(f"{part}-" for _, parts in runs[: numeric[0]] for part in parts)
    suffix = "".join(f"-{part}" for _, parts in runs[numeric[0] + 1 :] for part in parts)
    return re.compile(re.escape(prefix) + r"(\d+(?:-\d+)*)" + re.escape(suffix))


def version(model_family: re.Pattern, model: str) -> Version | None:
    match = model_family.fullmatch(model)
    return tuple(int(part) for part in match.group(1).split("-")) if match else None


def newer_models(current: str, available: list[str]) -> list[str] | None:
    """Models in current's family with a higher version, newest first. None if current has no clear version."""
    model_family = family(current)
    if model_family is None:
        return None
    current_version = version(model_family, current)
    newer = [(v, m) for m in available if (v := version(model_family, m)) is not None and v > current_version]
    return [m for _, m in sorted(newer, reverse=True)]
