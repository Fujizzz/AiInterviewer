from pathlib import Path

from rag.registry import filter_sources, get_source, load_registry, validate_registry

SOURCES_YAML = Path(__file__).resolve().parents[2] / "rag" / "data" / "sources.yaml"


def test_registry_loads_without_error():
    sources = load_registry(SOURCES_YAML)
    assert len(sources) > 20


def test_registry_has_no_validation_errors():
    sources = load_registry(SOURCES_YAML)
    errors = validate_registry(sources)
    assert errors == []


def test_registry_has_no_duplicate_names():
    sources = load_registry(SOURCES_YAML)
    names = [s.name for s in sources]
    assert len(names) == len(set(names))


def test_filter_by_priority_p0():
    sources = load_registry(SOURCES_YAML)
    p0 = filter_sources(sources, priority="P0")
    assert len(p0) > 0
    assert all(s.priority == "P0" for s in p0)


def test_get_known_source_by_name():
    sources = load_registry(SOURCES_YAML)
    src = get_source(sources, "cp-algorithms")
    assert src is not None
    assert src.loader == "git"


def test_reference_only_sources_are_flagged():
    sources = load_registry(SOURCES_YAML)
    ref_only = [s for s in sources if s.reference_only]
    assert len(ref_only) >= 5
    for s in ref_only:
        assert s.trust_level == "reference_only"
