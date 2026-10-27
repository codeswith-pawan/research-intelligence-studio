from unittest.mock import MagicMock, patch

from core.llm_wrapper import (
    build_cached_messages,
    call_llm,
    resolve_max_tokens,
)


def test_build_cached_messages_preserves_prompts():
    messages = build_cached_messages(
        system_prompt="You are a reviewer.",
        paper_content="Sample paper content.",
        criterion_prompt="Assess empirical rigor.",
    )

    assert messages[0]["role"] == "system"
    assert "Sample paper content." in messages[0]["content"][0]["text"]
    assert messages[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert messages[1] == {
        "role": "user",
        "content": "Assess empirical rigor.",
    }


def test_resolve_max_tokens_uses_role_environment_override(monkeypatch):
    monkeypatch.setenv("MAX_TOKENS_EXTRACTION", "2048")

    result = resolve_max_tokens(
        provider="custom_openai",
        model="llama3.2:3b",
        role="extraction",
    )

    assert result == 2048


def test_custom_provider_uses_bypass():
    expected = {
        "success": True,
        "content": "Mocked response",
        "usage": {},
        "cost": 0.0,
    }

    with patch(
        "core.llm_wrapper._call_custom_ollama_bypass",
        return_value=expected,
    ) as mock_bypass:
        result = call_llm(
            prompt="Review this paper.",
            system_prompt="You are a reviewer.",
            provider="custom_openai",
            model="llama3.2:3b",
            temperature=0.2,
            max_retries=1,
        )

    assert result == expected
    mock_bypass.assert_called_once()


def test_standard_provider_returns_mocked_response():
    message = MagicMock()
    message.content = "Mocked review"

    response = MagicMock()
    response.choices = [MagicMock(message=message)]
    response.usage.model_dump.return_value = {
        "prompt_tokens": 10,
        "completion_tokens": 5,
    }

    expected_usage = {
        "prompt_tokens": 10,
        "completion_tokens": 5,
    }

    with (
        patch("core.llm_wrapper.completion", return_value=response),
        patch("core.llm_wrapper.completion_cost", return_value=0.0),
    ):
        result = call_llm(
            prompt="Review this paper.",
            system_prompt="You are a reviewer.",
            provider="openai",
            model="gpt-4o-mini",
            temperature=0.2,
            max_retries=1,
        )

    assert result["success"] is True
    assert result["content"] == "Mocked review"
    assert result["usage"] == expected_usage
    assert result["cost"] == 0.0


def test_llm_failure_returns_safe_error_result():
    with patch(
        "core.llm_wrapper._call_custom_ollama_bypass",
        side_effect=RuntimeError("Mock connection failure"),
    ):
        try:
            call_llm(
                prompt="Review this paper.",
                system_prompt="You are a reviewer.",
                provider="custom_openai",
                model="llama3.2:3b",
                temperature=0.2,
                max_retries=1,
            )
        except RuntimeError as exc:
            assert str(exc) == "Mock connection failure"
        else:
            raise AssertionError("Expected the custom bypass exception to propagate")


def test_ollama_routing_failure_does_not_fall_back_to_paid_or_custom_provider():
    with (
        patch("core.llm_wrapper.completion", side_effect=RuntimeError("local model unavailable")) as mock_completion,
        patch("core.llm_wrapper.completion_cost", return_value=0.0),
        patch("core.llm_wrapper._call_custom_ollama_bypass") as mock_custom,
    ):
        result = call_llm(
            prompt="Review this paper.",
            system_prompt="You are a reviewer.",
            provider="ollama",
            model="llama3.2:3b",
            temperature=0.0,
            max_retries=0,
            role="extraction",
        )

    assert result["success"] is False
    assert result["content"] is None
    assert mock_completion.call_args.kwargs["model"] == "ollama/llama3.2:3b"
    mock_custom.assert_not_called()


def test_ollama_extraction_output_budget_is_capped_for_short_runtime_context(monkeypatch):
    monkeypatch.setenv("MAX_TOKENS_EXTRACTION", "4096")
    message = MagicMock()
    message.content = "{\"score\": 3}"
    response = MagicMock()
    response.choices = [MagicMock(message=message)]
    response.usage.model_dump.return_value = {"prompt_tokens": 10, "completion_tokens": 5}

    with (
        patch("core.llm_wrapper.completion", return_value=response) as mock_completion,
        patch("core.llm_wrapper.completion_cost", return_value=0.0),
    ):
        result = call_llm(
            prompt="Extract evidence.",
            system_prompt="Return JSON only.",
            provider="ollama",
            model="llama3.2:3b",
            temperature=0.0,
            max_retries=0,
            role="extraction",
        )

    assert result["success"] is True
    assert mock_completion.call_args.kwargs["max_tokens"] == 1024


def test_ollama_json_mode_uses_native_json_object_format():
    message = MagicMock()
    message.content = "{}"
    response = MagicMock()
    response.choices = [MagicMock(message=message)]
    response.usage.model_dump.return_value = {}

    with (
        patch("core.llm_wrapper.completion", return_value=response) as mock_completion,
        patch("core.llm_wrapper.completion_cost", return_value=0.0),
    ):
        result = call_llm(
            prompt="Return JSON.",
            system_prompt="Return one object.",
            provider="ollama",
            model="llama3.2:3b",
            temperature=0.0,
            max_retries=0,
            response_format="json",
            role="extraction",
        )

    assert result["success"] is True
    assert mock_completion.call_args.kwargs["response_format"] == {"type": "json_object"}
