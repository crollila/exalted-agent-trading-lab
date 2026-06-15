import pytest

from src.agents.llm_provider import (
    LLMProviderConfig,
    LLMProviderError,
    OpenAIProvider,
    build_provider,
    generate_structured,
    parse_structured_output,
)


def test_missing_openai_key_fails_safely():
    config = LLMProviderConfig(provider="openai", openai_api_key=None)
    with pytest.raises(LLMProviderError, match="OPENAI_API_KEY"):
        build_provider(config)


def test_missing_anthropic_key_fails_safely():
    config = LLMProviderConfig(provider="anthropic", anthropic_api_key=None)
    with pytest.raises(LLMProviderError, match="ANTHROPIC_API_KEY"):
        build_provider(config)


def test_unsupported_provider_fails():
    with pytest.raises(LLMProviderError, match="Unsupported"):
        build_provider(LLMProviderConfig(provider="gemini"))


def test_parse_rejects_non_json():
    with pytest.raises(LLMProviderError, match="non-JSON"):
        parse_structured_output("not json", "proposal")


def test_parse_rejects_non_object():
    with pytest.raises(LLMProviderError, match="must be a JSON object"):
        parse_structured_output("[1, 2, 3]", "proposal")


def test_parse_rejects_unknown_kind():
    with pytest.raises(LLMProviderError, match="Unsupported output kind"):
        parse_structured_output("{}", "trade")


class FakeOpenAIClient:
    """Mocked OpenAI client; no real network calls."""

    def __init__(self, config):
        self.chat = self

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        from types import SimpleNamespace

        content = '{"proposals": [], "portfolio_notes": "ok"}'
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def test_openai_provider_with_mock_returns_structured_json():
    config = LLMProviderConfig(provider="openai", openai_api_key="test-key")
    provider = OpenAIProvider(config=config, client_factory=lambda c: FakeOpenAIClient(c))
    data = generate_structured(provider, "proposal", "system", "user")
    assert data == {"proposals": [], "portfolio_notes": "ok"}


def test_provider_has_no_broker_access():
    provider = OpenAIProvider(config=LLMProviderConfig(openai_api_key="k"))
    assert not hasattr(provider, "submit_order")
    assert not hasattr(provider, "submit_paper_order")
