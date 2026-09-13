"""llm_editor.py -- слой вызова LLM (_client_and_model/request_plan/
edit_scene_with_text). НИ ОДИН тест здесь не должен коснуться настоящей сети:
живой вызов Yandex Cloud или Gemini стоит пользователю реальных денег (см.
memory feedback-llm-api-cost) -- клиент openai подменяется фальшивым объектом
с тем же интерфейсом (.responses.create(...) для yandex, .chat.completions.
create(...) для gemini), _client_and_model мокается напрямую там, где нужен
весь путь целиком (edit_scene_with_text)."""

from types import SimpleNamespace

import llm_editor
import pytest
from llm_editor import (
    LlmError,
    LlmNotConfiguredError,
    LlmPlan,
    _client_and_model,
    _extract_json,
    edit_scene_with_text,
    request_plan,
)
from placement import Placer
from plant_catalog import load_catalog

CATALOG = load_catalog()


@pytest.fixture
def scene1(location_scene):
    return location_scene[1].model_copy(deep=True)


# --- _client_and_model ---------------------------------------------------


def test_client_and_model_raises_when_not_configured(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "yandex")
    monkeypatch.delenv("YANDEX_CLOUD_API_KEY", raising=False)
    monkeypatch.delenv("YANDEX_CLOUD_FOLDER", raising=False)
    with pytest.raises(LlmNotConfiguredError):
        _client_and_model()


def test_client_and_model_raises_when_only_api_key_set(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "yandex")
    monkeypatch.setenv("YANDEX_CLOUD_API_KEY", "fake-key")
    monkeypatch.delenv("YANDEX_CLOUD_FOLDER", raising=False)
    with pytest.raises(LlmNotConfiguredError):
        _client_and_model()


def test_client_and_model_builds_model_uri_from_folder_and_model_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "yandex")
    monkeypatch.setenv("YANDEX_CLOUD_API_KEY", "fake-key")
    monkeypatch.setenv("YANDEX_CLOUD_FOLDER", "folder123")
    monkeypatch.setenv("YANDEX_CLOUD_MODEL", "yandexgpt/rc")
    _client, model = _client_and_model()
    assert model == "gpt://folder123/yandexgpt/rc"


def test_client_and_model_defaults_model_when_env_var_missing(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "yandex")
    monkeypatch.setenv("YANDEX_CLOUD_API_KEY", "fake-key")
    monkeypatch.setenv("YANDEX_CLOUD_FOLDER", "folder123")
    monkeypatch.delenv("YANDEX_CLOUD_MODEL", raising=False)
    _client, model = _client_and_model()
    assert model == "gpt://folder123/yandexgpt/latest"


def test_client_and_model_defaults_to_gemini_when_provider_env_var_missing(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    client, model = _client_and_model()
    assert model == "gemini-3.6-flash"
    assert str(client.base_url).startswith("https://generativelanguage.googleapis.com")


def test_client_and_model_raises_when_gemini_selected_without_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(LlmNotConfiguredError):
        _client_and_model()


def test_client_and_model_uses_gemini_when_selected(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.1-pro")
    client, model = _client_and_model()
    assert model == "gemini-3.1-pro"
    assert str(client.base_url).startswith("https://generativelanguage.googleapis.com")


def test_client_and_model_defaults_gemini_model_when_env_var_missing(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    _client, model = _client_and_model()
    assert model == "gemini-3.6-flash"


# --- _extract_json ---------------------------------------------------------


def test_extract_json_parses_plain_json():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_strips_markdown_fence_and_leading_text():
    text = 'Вот план:\n```json\n{"a": 1, "b": [1, 2]}\n```\nГотово.'
    assert _extract_json(text) == {"a": 1, "b": [1, 2]}


def test_extract_json_raises_when_no_braces_at_all():
    with pytest.raises(LlmError, match="не вернула JSON"):
        _extract_json("просто текст без плана")


def test_extract_json_raises_on_malformed_json_between_braces():
    with pytest.raises(LlmError, match="некорректный JSON"):
        _extract_json("{not valid json, oops}")


# --- request_plan: сеть подменяется целиком -----------------------------


class _FakeResponses:
    def __init__(self, response):
        self._response = response

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return self._response


class _FakeClient:
    def __init__(self, response):
        self.responses = _FakeResponses(response)


def _ok_response(output_text: str, input_tokens=100, output_tokens=50):
    return SimpleNamespace(
        status="completed",
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
        output_text=output_text,
        incomplete_details=None,
    )


def test_request_plan_parses_a_valid_response(monkeypatch, scene1):
    monkeypatch.setenv("LLM_PROVIDER", "yandex")
    response = _ok_response('{"operations": [{"op": "remove", "id": "lamp_001"}], "explanation": "убрал фонарь"}')
    monkeypatch.setattr(llm_editor, "_client_and_model", lambda: (_FakeClient(response), "fake-model"))
    placer = Placer(scene1)
    plan = request_plan(scene1, "убери фонарь", CATALOG, placer)
    assert plan.explanation == "убрал фонарь"
    assert plan.operations == [{"op": "remove", "id": "lamp_001"}]


def test_request_plan_raises_llm_error_when_client_raises_openai_error(monkeypatch, scene1):
    monkeypatch.setenv("LLM_PROVIDER", "yandex")
    import openai

    class _RaisingResponses:
        def create(self, **kwargs):
            raise openai.APIConnectionError(request=SimpleNamespace())

    class _RaisingClient:
        responses = _RaisingResponses()

    monkeypatch.setattr(llm_editor, "_client_and_model", lambda: (_RaisingClient(), "fake-model"))
    placer = Placer(scene1)
    with pytest.raises(LlmError, match="LLM недоступна"):
        request_plan(scene1, "что угодно", CATALOG, placer)


def test_request_plan_raises_when_response_incomplete_due_to_token_limit(monkeypatch, scene1):
    monkeypatch.setenv("LLM_PROVIDER", "yandex")
    response = SimpleNamespace(
        status="incomplete",
        usage=SimpleNamespace(input_tokens=10, output_tokens=8000),
        output_text="",
        incomplete_details=SimpleNamespace(reason="max_output_tokens"),
    )
    monkeypatch.setattr(llm_editor, "_client_and_model", lambda: (_FakeClient(response), "fake-model"))
    placer = Placer(scene1)
    with pytest.raises(LlmError, match="не уложилась в лимит"):
        request_plan(scene1, "что угодно", CATALOG, placer)


def test_request_plan_raises_when_response_incomplete_for_other_reason(monkeypatch, scene1):
    monkeypatch.setenv("LLM_PROVIDER", "yandex")
    response = SimpleNamespace(
        status="incomplete",
        usage=SimpleNamespace(input_tokens=10, output_tokens=20),
        output_text="",
        incomplete_details=SimpleNamespace(reason="content_filter"),
    )
    monkeypatch.setattr(llm_editor, "_client_and_model", lambda: (_FakeClient(response), "fake-model"))
    placer = Placer(scene1)
    with pytest.raises(LlmError, match="content_filter"):
        request_plan(scene1, "что угодно", CATALOG, placer)


def test_request_plan_raises_when_output_is_not_json(monkeypatch, scene1):
    monkeypatch.setenv("LLM_PROVIDER", "yandex")
    response = _ok_response("извините, не могу помочь")
    monkeypatch.setattr(llm_editor, "_client_and_model", lambda: (_FakeClient(response), "fake-model"))
    placer = Placer(scene1)
    with pytest.raises(LlmError, match="не вернула JSON"):
        request_plan(scene1, "что угодно", CATALOG, placer)


def test_request_plan_raises_when_json_does_not_match_plan_schema(monkeypatch, scene1):
    monkeypatch.setenv("LLM_PROVIDER", "yandex")
    # Валидный JSON, но "operations" -- не список (не подходит под LlmPlan).
    response = _ok_response('{"operations": "not-a-list"}')
    monkeypatch.setattr(llm_editor, "_client_and_model", lambda: (_FakeClient(response), "fake-model"))
    placer = Placer(scene1)
    with pytest.raises(LlmError, match="неожиданном формате"):
        request_plan(scene1, "что угодно", CATALOG, placer)


class _FakeChatCompletions:
    def __init__(self, response):
        self._response = response

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return self._response


class _FakeChatClient:
    def __init__(self, response):
        self.chat = SimpleNamespace(completions=_FakeChatCompletions(response))


def _ok_chat_response(content: str, prompt_tokens=100, completion_tokens=50, finish_reason="stop"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


def test_request_plan_parses_a_valid_response_from_gemini(monkeypatch, scene1):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    response = _ok_chat_response('{"operations": [{"op": "remove", "id": "lamp_001"}], "explanation": "убрал фонарь"}')
    monkeypatch.setattr(llm_editor, "_client_and_model", lambda: (_FakeChatClient(response), "gemini-3.6-flash"))
    placer = Placer(scene1)
    plan = request_plan(scene1, "убери фонарь", CATALOG, placer)
    assert plan.explanation == "убрал фонарь"
    assert plan.operations == [{"op": "remove", "id": "lamp_001"}]


def test_request_plan_raises_when_gemini_response_hits_token_limit(monkeypatch, scene1):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    response = _ok_chat_response("", finish_reason="length")
    monkeypatch.setattr(llm_editor, "_client_and_model", lambda: (_FakeChatClient(response), "gemini-3.6-flash"))
    placer = Placer(scene1)
    with pytest.raises(LlmError, match="не уложилась в лимит"):
        request_plan(scene1, "что угодно", CATALOG, placer)


def test_request_plan_propagates_not_configured_error(monkeypatch, scene1):
    monkeypatch.delenv("YANDEX_CLOUD_API_KEY", raising=False)
    monkeypatch.delenv("YANDEX_CLOUD_FOLDER", raising=False)
    placer = Placer(scene1)
    with pytest.raises(LlmNotConfiguredError):
        request_plan(scene1, "что угодно", CATALOG, placer)


# --- edit_scene_with_text: полный путь, request_plan подменяется целиком ----


def test_edit_scene_with_text_applies_the_returned_plan(monkeypatch, scene1):
    fake_plan = LlmPlan(operations=[{"op": "remove", "id": "lamp_001"}], explanation="убрал фонарь")
    monkeypatch.setattr(llm_editor, "request_plan", lambda scene, instruction, catalog, placer: fake_plan)
    result = edit_scene_with_text(scene1, "убери фонарь")
    assert "lamp_001" not in {o.id for o in result.scene.objects}
    assert result.explanation == "убрал фонарь"


def test_edit_scene_with_text_propagates_llm_not_configured(monkeypatch, scene1):
    def _raise(*a, **kw):
        raise LlmNotConfiguredError("не настроено")

    monkeypatch.setattr(llm_editor, "request_plan", _raise)
    with pytest.raises(LlmNotConfiguredError):
        edit_scene_with_text(scene1, "что угодно")


def test_edit_scene_with_text_propagates_llm_error(monkeypatch, scene1):
    def _raise(*a, **kw):
        raise LlmError("недоступна")

    monkeypatch.setattr(llm_editor, "request_plan", _raise)
    with pytest.raises(LlmError):
        edit_scene_with_text(scene1, "что угодно")
