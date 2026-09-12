"""metrics.py -- сами счётчики Prometheus. Не проверяем формат экспозиции
(это ответственность prometheus_client/prometheus-fastapi-instrumentator,
проверяется по-настоящему в test_main.py через GET /metrics), только то, что
инкременты действительно применяются и метки работают ожидаемо."""

import metrics
from prometheus_client import REGISTRY


def _sample_value(metric_name: str, labels: dict | None = None) -> float:
    for family in REGISTRY.collect():
        if family.name != metric_name.removesuffix("_total"):
            continue
        for sample in family.samples:
            if not sample.name.endswith("_total"):
                continue
            if labels is None or all(sample.labels.get(k) == v for k, v in labels.items()):
                return sample.value
    return 0.0


def test_dxf_parses_counter_increments():
    before = _sample_value("greencity_dxf_parses_total")
    metrics.dxf_parses_total.inc()
    after = _sample_value("greencity_dxf_parses_total")
    assert after == before + 1


def test_dxf_parse_errors_counter_increments():
    before = _sample_value("greencity_dxf_parse_errors_total")
    metrics.dxf_parse_errors_total.inc()
    assert _sample_value("greencity_dxf_parse_errors_total") == before + 1


def test_dxf_exports_counter_increments():
    before = _sample_value("greencity_dxf_exports_total")
    metrics.dxf_exports_total.inc()
    assert _sample_value("greencity_dxf_exports_total") == before + 1


def test_greenery_generated_counter_has_independent_labels():
    before_tree = _sample_value("greencity_greenery_generated_total", {"object_type": "tree"})
    before_bush = _sample_value("greencity_greenery_generated_total", {"object_type": "bush"})
    metrics.greenery_generated_total.labels(object_type="tree").inc(5)
    assert _sample_value("greencity_greenery_generated_total", {"object_type": "tree"}) == before_tree + 5
    assert _sample_value("greencity_greenery_generated_total", {"object_type": "bush"}) == before_bush  # не задет


def test_llm_edit_requests_counter_by_outcome():
    before = _sample_value("greencity_llm_edit_requests_total", {"outcome": "success"})
    metrics.llm_edit_requests_total.labels(outcome="success").inc()
    assert _sample_value("greencity_llm_edit_requests_total", {"outcome": "success"}) == before + 1


def test_auth_registrations_counter_by_outcome():
    before = _sample_value("greencity_auth_registrations_total", {"outcome": "rejected"})
    metrics.auth_registrations_total.labels(outcome="rejected").inc()
    assert _sample_value("greencity_auth_registrations_total", {"outcome": "rejected"}) == before + 1


def test_auth_logins_counter_by_outcome():
    before = _sample_value("greencity_auth_logins_total", {"outcome": "rate_limited"})
    metrics.auth_logins_total.labels(outcome="rate_limited").inc()
    assert _sample_value("greencity_auth_logins_total", {"outcome": "rate_limited"}) == before + 1


def test_rate_limit_blocks_counter_by_endpoint():
    before = _sample_value("greencity_rate_limit_blocks_total", {"endpoint": "login"})
    metrics.rate_limit_blocks_total.labels(endpoint="login").inc()
    assert _sample_value("greencity_rate_limit_blocks_total", {"endpoint": "login"}) == before + 1
