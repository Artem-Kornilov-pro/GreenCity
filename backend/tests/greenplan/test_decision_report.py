"""greenplan/decision_report.py -- Этап 6 GreenPlan: связный текст-объяснение решений
через LLM (YandexGPT в Yandex AI Studio). Ни один тест не должен трогать
настоящую сеть -- тот же принцип, что и в text_editor/test_llm_client.py (см.
его докстринг): клиент openai подменяется фальшивым объектом с тем же
интерфейсом (.responses.create(...)), платный API здесь не нужен."""

from types import SimpleNamespace

import openai
import pytest

from greenplan import decision_report
from greenplan.decision_report import DecisionReportUnavailable, _summarize, generate_report
from greenplan.pattern_assignment import ZoneAssignment


def _assignment(
    zone_id,
    zone_kind="open_area",
    pattern_id="grove_clusters",
    source_project="12_natashinsky_proezd",
    source_quote="цитата",
    confidence=0.67,
):
    return ZoneAssignment(
        zone_id=zone_id,
        zone_kind=zone_kind,
        pattern_id=pattern_id,
        source_project=source_project,
        source_quote=source_quote,
        confidence=confidence,
    )


# --- _summarize ---------------------------------------------------------------


def test_summarize_collapses_same_group_into_one_row_with_correct_count():
    assignments = [_assignment("z1"), _assignment("z2"), _assignment("z3")]
    rows = _summarize(assignments)
    assert len(rows) == 1
    assert rows[0].zone_count == 3


def test_summarize_keeps_different_source_projects_as_separate_rows():
    assignments = [
        _assignment("z1", source_project="12_natashinsky_proezd"),
        _assignment("z2", source_project="13_kharkovsky_proezd"),
    ]
    rows = _summarize(assignments)
    assert len(rows) == 2
    assert {r.source_project for r in rows} == {"12_natashinsky_proezd", "13_kharkovsky_proezd"}


def test_summarize_keeps_fallback_assignments_without_source_project_separate():
    assignments = [_assignment("z1", source_project="12_natashinsky_proezd"), _assignment("z2", source_project=None)]
    rows = _summarize(assignments)
    assert len(rows) == 2
    assert None in {r.source_project for r in rows}


def test_summarize_sorted_by_zone_count_descending():
    assignments = [
        _assignment("z1", zone_kind="path_corridor", pattern_id="linear_hedge_row", source_project="19_2ya_pryadilnaya"),
        _assignment("z2", zone_kind="open_area", pattern_id="grove_clusters", source_project="12_natashinsky_proezd"),
        _assignment("z3", zone_kind="open_area", pattern_id="grove_clusters", source_project="12_natashinsky_proezd"),
    ]
    rows = _summarize(assignments)
    assert rows[0].zone_count == 2
    assert rows[0].zone_kind == "open_area"


def test_summarize_takes_the_highest_confidence_within_a_group_as_representative():
    assignments = [_assignment("z1", confidence=0.33), _assignment("z2", confidence=0.67)]
    rows = _summarize(assignments)
    assert rows[0].confidence == pytest.approx(0.67)


# --- generate_report: fake LLM client, никакой настоящей сети ----------------


class _FakeResponses:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        if self._error is not None:
            raise self._error
        return self._response


class _FakeClient:
    def __init__(self, response=None, error=None):
        self.responses = _FakeResponses(response, error)


def _ok_response(content: str, status: str = "completed", reason: str | None = None):
    return SimpleNamespace(
        status=status,
        output_text=content,
        incomplete_details=SimpleNamespace(reason=reason) if reason else None,
    )


async def test_generate_report_returns_none_for_empty_assignments_without_calling_client(monkeypatch):
    def _fail():
        raise AssertionError("клиент не должен вызываться, когда описывать нечего")

    monkeypatch.setattr(decision_report, "_client", _fail)
    assert await generate_report([]) is None


async def test_generate_report_returns_llm_text_on_success(monkeypatch):
    text = "Вдоль дорожек участка использована живая изгородь по аналогии с похожим проектом."
    monkeypatch.setattr(decision_report, "_client", lambda: _FakeClient(response=_ok_response(text)))
    assert await generate_report([_assignment("z1")]) == text


async def test_generate_report_strips_whitespace_from_response(monkeypatch):
    monkeypatch.setattr(decision_report, "_client", lambda: _FakeClient(response=_ok_response("  текст с пробелами  \n")))
    assert await generate_report([_assignment("z1")]) == "текст с пробелами"


async def test_generate_report_raises_when_client_errors(monkeypatch):
    error = openai.APIConnectionError(request=SimpleNamespace())
    monkeypatch.setattr(decision_report, "_client", lambda: _FakeClient(error=error))
    with pytest.raises(DecisionReportUnavailable):
        await generate_report([_assignment("z1")])


async def test_generate_report_raises_when_response_is_empty(monkeypatch):
    monkeypatch.setattr(decision_report, "_client", lambda: _FakeClient(response=_ok_response("")))
    with pytest.raises(DecisionReportUnavailable):
        await generate_report([_assignment("z1")])


async def test_generate_report_passes_model_and_temperature_to_client(monkeypatch):
    fake = _FakeClient(response=_ok_response("текст"))
    monkeypatch.setattr(decision_report, "_client", lambda: fake)
    monkeypatch.setenv("YANDEX_CLOUD_FOLDER", "folder123")
    await generate_report([_assignment("z1")])
    kwargs = fake.responses.last_kwargs
    assert kwargs["model"] == "gpt://folder123/yandexgpt-5.1/latest"
    assert kwargs["temperature"] == decision_report.TEMPERATURE
    assert kwargs["instructions"] == decision_report.INSTRUCTIONS


async def test_model_comes_from_env(monkeypatch):
    fake = _FakeClient(response=_ok_response("текст"))
    monkeypatch.setattr(decision_report, "_client", lambda: fake)
    monkeypatch.setenv("YANDEX_CLOUD_FOLDER", "folder123")
    monkeypatch.setenv("YANDEX_CLOUD_REPORT_MODEL", "yandexgpt-5-lite/latest")
    await generate_report([_assignment("z1")])
    assert fake.responses.last_kwargs["model"] == "gpt://folder123/yandexgpt-5-lite/latest"


async def test_generate_report_raises_when_answer_is_cut_off(monkeypatch):
    response = _ok_response("Текст, оборванный на полу", status="incomplete", reason="max_output_tokens")
    monkeypatch.setattr(decision_report, "_client", lambda: _FakeClient(response=response))
    with pytest.raises(DecisionReportUnavailable, match="max_output_tokens"):
        await generate_report([_assignment("z1")])


# --- Настройки Yandex AI Studio: ключ и каталог ------------------------------


async def test_report_is_unavailable_without_key_or_folder(monkeypatch):
    # _clean_llm_env (conftest.py) уже убрал ключи настоящего .env.
    with pytest.raises(DecisionReportUnavailable, match="YANDEX_CLOUD_API_KEY"):
        await generate_report([_assignment("z1")])
    monkeypatch.setenv("YANDEX_CLOUD_API_KEY", "fake-key")
    with pytest.raises(DecisionReportUnavailable, match="YANDEX_CLOUD_FOLDER"):
        await generate_report([_assignment("z1")])


def test_client_points_at_yandex_ai_studio(monkeypatch):
    monkeypatch.setenv("YANDEX_CLOUD_API_KEY", "fake-key")
    monkeypatch.setenv("YANDEX_CLOUD_FOLDER", "folder123")
    client = decision_report._client()
    assert str(client.base_url).startswith("https://ai.api.cloud.yandex.net/v1")
    assert (client.api_key, client.project) == ("fake-key", "folder123")
    assert client.max_retries == decision_report.MAX_RETRIES


async def test_prompt_starts_with_the_site_decision(monkeypatch):
    client = _FakeClient(response=_ok_response("текст"))
    monkeypatch.setattr(decision_report, "_client", lambda: client)
    assignment = _assignment("z1").model_copy(update={"site_style": "landscape", "lead_project": "12_natashinsky_proezd"})
    await generate_report([assignment])
    user = client.responses.last_kwargs["input"]
    first_fact = user.splitlines()[1]
    assert first_fact.startswith("Общее решение: стиль участка -- пейзажный")
    assert "12_natashinsky_proezd" in first_fact
