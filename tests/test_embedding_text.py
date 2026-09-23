from copy import deepcopy
from types import SimpleNamespace as Obj

import pytest

from app.catalog import embed_text
from app.config import EMBED_MODEL
from app.embedding_text import MAX_INPUT_TOKENS, encoding, input_tokens
from app.index_build import embed_texts
from app.search import _embed_query


def test_only_stable_search_fields_affect_text():
    product = {"name": "Автомат 16А", "article": "a1", "price": 1, "quantity": 2,
               "category": "nizkovoltnaya_apparatura / avtomaty",
               "description": "<p>Защита &amp; управление</p>",
               "properties": {"TORGOVAYA_MARKA": "IEK", "NOMINALNYY_TOK": "16А",
                              "VYKHODNOE_NAPRYAZHENIE_": "230В"}}
    original = deepcopy(product)
    text = embed_text(product)
    assert "Номинальный ток: 16А" in text
    assert "Бренд: IEK" in text
    assert "Категория: Низковольтная аппаратура" in text
    assert "Выходное напряжение: 230В" in text
    assert "Описание: Защита & управление" in text
    assert product == original
    product.update(price=999, quantity=800, stores=[{"quantity": 100}], image="https://image")
    product["properties"].update({
        "SLIDER_PHOTOS": "101519 | 101520", "POKAZYVAT_TSENY": "Нет",
        "KOL_VO_U_POSTAVSHCHIKA": 96, "KOL_VO_CHASOV_DOSTAVKI_OT_POSTAVSHCHIKA": "72 часа",
        "KRATNOST_MAKS": 500, "CML2_BAR_CODE": "123456", "FILES_CERTIFICATES": [99],
        "WB_SPECIFICATIONS": '{"weight": 123}', "UNKNOWN_FUTURE_FIELD": "noise",
    })
    product["properties"] = dict(reversed(list(product["properties"].items())))
    assert embed_text(product) == text
    product["properties"]["NOMINALNYY_TOK"] = "32А"
    assert embed_text(product) != text


def test_promotional_category_is_not_embedded():
    text = embed_text({"name": "Лампа", "category": "spets_predlozhenie / aktsiya",
                       "properties": {"MOSHCHNOST_W": 0, "TSVET_1": "Белый"}})
    assert "spets" not in text and "aktsiya" not in text
    assert "Мощность, Вт: 0" in text
    assert "Цвет: Белый" in text


def test_token_truncation_preserves_specs_before_long_description():
    text = embed_text({"name": "Автомат", "properties": {"NOMINALNYY_TOK": "16А"},
                       "description": "Длинное описание. " * 9000})
    tokens = input_tokens(text, EMBED_MODEL, truncate=True)
    assert len(tokens) == MAX_INPUT_TOKENS
    assert "Номинальный ток: 16А" in encoding(EMBED_MODEL).decode(tokens)
    with pytest.raises(ValueError, match="token limit"):
        input_tokens(text, EMBED_MODEL)


def test_special_token_strings_are_treated_as_catalog_text():
    text = "Лампа <|endoftext|>"
    assert encoding(EMBED_MODEL).decode(input_tokens(text, EMBED_MODEL)) == text


def test_query_over_limit_rejected_before_provider_call(monkeypatch):
    def unexpected(**kwargs):
        pytest.fail("Oversized query reached provider")
    monkeypatch.setattr("app.search.provider_client", unexpected)
    with pytest.raises(ValueError, match="token limit"):
        _embed_query("Длинный запрос " * 9000, EMBED_MODEL)


def test_api_batches_respect_token_and_item_limits_and_resume(tmp_path):
    calls = []
    class Provider:
        def create(self, *, model, input):
            calls.append(input)
            assert len(input) <= 64
            assert sum(map(len, input)) <= 300_000
            assert all(0 < len(tokens) <= 8192 for tokens in input)
            # Reversed provider order must not change product/vector alignment.
            return Obj(data=[Obj(index=i, embedding=[1., float(i + 1), 1.])
                             for i, tokens in reversed(list(enumerate(input)))])
    texts = [f"Товар {i} " + "описание " * 9000 for i in range(70)]
    path = tmp_path / "cache.sqlite3"
    first = embed_texts(Obj(embeddings=Provider()), texts, path)
    assert len(first) == 70 and len(calls) > 1
    expected = [float(i + 1) for batch in calls for i in range(len(batch))]
    assert first[:, 1] / first[:, 0] == pytest.approx(expected)
    calls.clear()
    second = embed_texts(Obj(embeddings=Provider()), texts, path)
    assert not calls
    assert (first == second).all()
    embed_texts(Obj(embeddings=Provider()), [f"Короткий {i}" for i in range(65)], path)
    assert [len(batch) for batch in calls] == [64, 1]


def test_empty_text_does_not_call_provider(tmp_path):
    with pytest.raises(ValueError, match="empty"):
        embed_texts(None, [""], tmp_path / "cache.sqlite3")
