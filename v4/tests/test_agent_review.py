"""Keep the submitted LLM stages visible to the organizer's source review."""

from __future__ import annotations

import importlib.util

from v4.evaluation.package import ARCHES, VERSION, sources
from v4.evaluation.upstream import resolve


def test_project_source_review_includes_both_model_stages(tmp_path):
    for arch in ARCHES:
        (tmp_path / f"numpy-{VERSION}-cp312-cp312-test_{arch}.whl").write_bytes(b"wheel placeholder")
    review = resolve() / "scripts" / "review-agent-usage.py"
    spec = importlib.util.spec_from_file_location("gosim_v4_agent_review", review)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    selected, _ = module.select_files(sources(tmp_path))
    visible = {name for name, _content, truncated in selected if not truncated}
    assert {
        "README.md",
        "config.json",
        "main.py",
        "observer4/advice.py",
        "observer4/planning.py",
        "observer4/policy.py",
        "observer4/services.py",
        "observer4/llm/model_advisor.py",
    } <= visible
