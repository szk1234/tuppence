from tuppence.llm.catalogue import CatalogueEntry, ModelCatalogue, load_baseline, normalise_model_id


def test_normalise():
    assert normalise_model_id("anthropic/claude-sonnet-4.5") == "claude-sonnet-4-5"
    assert normalise_model_id("claude-3-5-haiku-20241022") == "claude-3-5-haiku"
    assert normalise_model_id("Meta-Llama/Llama-3.1-8B-Instruct:free") == "llama-3-1-8b-instruct"


def test_lookup_exact_alias_and_prefix():
    cat = ModelCatalogue(
        [
            CatalogueEntry(id="vendor/model-a", aliases=["model-a"], context_window=1000),
            CatalogueEntry(id="vendor/model-a-long", aliases=[], context_window=2000),
        ]
    )
    assert cat.lookup("model-a").context_window == 1000
    assert cat.lookup("vendor/model-a-long").context_window == 2000
    assert cat.lookup("model-a-long-2026-preview").context_window == 2000  # longest prefix
    assert cat.lookup("unrelated") is None


def test_later_entries_win():
    cat = ModelCatalogue(
        [
            CatalogueEntry(id="x/m", context_window=1),
            CatalogueEntry(id="y/m", context_window=2),
        ]
    )
    assert cat.lookup("m").context_window == 2


def test_baseline_loads_and_has_anthropic():
    cat = load_baseline()
    e = cat.lookup("claude-sonnet-5-5")
    assert e is not None
    assert e.context_window == 1_000_000
    assert e.price_in_usd_per_mtok == 2.0
    assert all((x.price_in_usd_per_mtok or 0) >= 0 for x in cat.by_key.values())
