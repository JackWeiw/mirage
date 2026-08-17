"""Tests for AgentCore.revise_instruction (mock-agent, no real LLM)."""

from agent.agent_core import PROMPTS_DIR


def test_revise_instruction_prompt_exists_and_has_placeholders() -> None:
    text = (PROMPTS_DIR / "revise_instruction.md").read_text()
    assert "{prior_instruction}" in text
    assert "{report}" in text
    assert "{sensitivity}" in text
    assert "{recent_history}" in text
    # The prompt must state the proven-direction constraint and the output schema.
    assert "proven direction" in text.lower()
    assert "revised_instruction" in text
    assert "adjustments" in text
