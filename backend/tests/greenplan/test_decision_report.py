"""greenplan/decision_report.py -- Этап 6 GreenPlan: связный текст-объяснение решений
через LLM (mistral:7b/Ollama). Ни один тест не должен трогать настоящую сеть
-- тот же принцип, что и в text_editor/test_llm_client.py (см. его докстринг):
клиент openai подменяется фальшивым объектом с тем же интерфейсом
(.chat.completions.create(...)), никакого реального Ollama здесь не нужно."""

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


class _FakeChatCompletions:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        if self._error is not None:
            raise self._error
        return self._response


class _FakeChatClient:
    def __init__(self, response=None, error=None):
        self.chat = SimpleNamespace(completions=_FakeChatCompletions(response, error))


def _ok_response(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def test_generate_report_returns_none_for_empty_assignments_without_calling_client(monkeypatch):
    def _fail():
        raise AssertionError("клиент не должен вызываться, когда описывать нечего")

    monkeypatch.setattr(decision_report, "_client", _fail)
    assert generate_report([]) is None


def test_generate_report_returns_llm_text_on_success(monkeypatch):
    text = "Вдоль дорожек участка использована живая изгородь по аналогии с похожим проектом."
    monkeypatch.setattr(decision_report, "_client", lambda: _FakeChatClient(response=_ok_response(text)))
    assert generate_report([_assignment("z1")]) == text


def test_generate_report_strips_whitespace_from_response(monkeypatch):
    monkeypatch.setattr(decision_report, "_client", lambda: _FakeChatClient(response=_ok_response("  текст с пробелами  \n")))
    assert generate_report([_assignment("z1")]) == "текст с пробелами"


def test_generate_report_raises_when_client_errors(monkeypatch):
    error = openai.APIConnectionError(request=SimpleNamespace())
    monkeypatch.setattr(decision_report, "_client", lambda: _FakeChatClient(error=error))
    with pytest.raises(DecisionReportUnavailable):
        generate_report([_assignment("z1")])


def test_generate_report_raises_when_response_is_empty(monkeypatch):
    monkeypatch.setattr(decision_report, "_client", lambda: _FakeChatClient(response=_ok_response("")))
    with pytest.raises(DecisionReportUnavailable):
        generate_report([_assignment("z1")])


def test_generate_report_passes_model_and_temperature_to_client(monkeypatch):
    fake = _FakeChatClient(response=_ok_response("текст"))
    monkeypatch.setattr(decision_report, "_client", lambda: fake)
    generate_report([_assignment("z1")])
    kwargs = fake.chat.completions.last_kwargs
    assert kwargs["model"] == decision_report.OLLAMA_MODEL
    assert kwargs["temperature"] == decision_report.TEMPERATURE


def test_prompt_starts_with_the_site_decision(monkeypatch):
    client = _FakeChatClient(response=_ok_response("текст"))
    monkeypatch.setattr(decision_report, "_client", lambda: client)
    assignment = _assignment("z1").model_copy(update={"site_style": "landscape", "lead_project": "12_natashinsky_proezd"})
    generate_report([assignment])
    user = client.chat.completions.last_kwargs["messages"][1]["content"]
    first_fact = user.splitlines()[1]
    assert first_fact.startswith("Общее решение: стиль участка -- пейзажный")
    assert "12_natashinsky_proezd" in first_fact
