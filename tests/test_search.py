import json

import numpy as np

from app.catalog import category_from_url, load_products
from app.search import CatalogIndex, exact_matches, search_products
from tests.helpers import sample_products, tiny_index


def test_category_from_url():
    url = (
        "https://ekt.kz/catalog/nizkovoltnaya_apparatura/"
        "silovye_avtomaticheskie_vyklyuchateli/drx250/item/"
    )
    assert "nizkovoltnaya_apparatura" in category_from_url(url)


def test_load_pages_and_details(tmp_path):
    pages = tmp_path / "pages"
    details = tmp_path / "details"
    pages.mkdir()
    details.mkdir()
    (pages / "00001.json").write_text(
        json.dumps(
            {
                "page": 1,
                "items": [
                    {
                        "id": 101,
                        "name": "Автомат DRX250",
                        "article": "200300285_",
                        "price": 64920,
                        "image": None,
                        "url": "https://ekt.kz/catalog/nizkovoltnaya_apparatura/avt/item/",
                        "offers": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (details / "101.json").write_text(
        json.dumps(
            {
                "id": 101,
                "name": "Автомат DRX250",
                "article": "200300285_",
                "price": 64920,
                "quantity": 23,
                "description": "3P 160А 18kA",
                "properties": {"TORGOVAYA_MARKA": "Legrand"},
                "stores": [{"name": "Алматы", "quantity": 5}],
            }
        ),
        encoding="utf-8",
    )
    products = load_products(tmp_path)
    assert len(products) == 1
    assert products[0]["quantity"] == 23
    assert products[0]["price"] == 64920
    assert "Legrand" in products[0]["spec_snippet"] or products[0]["description"]


def test_detail_precedence_and_observation_timestamp(tmp_path):
    (tmp_path / "products.json").write_text(json.dumps([
        {"id": 101, "name": "Автомат", "article": "a1", "price": "1.25", "quantity": 2}
    ]), encoding="utf-8")
    (tmp_path / "products.csv").write_text(
        "id;name;article;price;quantity\n101;Автомат;a1;1.30;3\n", encoding="utf-8")
    details = tmp_path / "details"
    details.mkdir()
    (details / "101.json").write_text(json.dumps({
        "id": 101, "name": "Автомат", "price": "1.35", "quantity": 4,
        "_source_observed_at": "2026-09-23T10:00:00+00:00",
    }), encoding="utf-8")
    product = load_products(tmp_path)[0]
    assert product["price"] == "1.35" and product["quantity"] == 4
    assert product["source_observed_at"] == "2026-09-23T10:00:00+00:00"


def test_load_semicolon_csv(tmp_path):
    (tmp_path / "products.csv").write_text(
        "id;article;name;price;quantity;description;image;url;stores;properties;offers\n"
        "515279;200300273_;Автомат DRX125;26930;36;Описание;"
        "https://img;https://ekt.kz/catalog/nizkovoltnaya_apparatura/avt/item/;"
        "Алматы=19 | Тараз=3;"
        "TORGOVAYA_MARKA=Legrand | NOMINALNYY_TOK=40А;\n",
        encoding="utf-8",
    )
    products = load_products(tmp_path)
    assert products[0]["quantity"] == 36
    assert products[0]["price"] == 26930
    assert products[0]["stores"][0]["name"] == "Алматы"
    assert products[0]["properties"]["TORGOVAYA_MARKA"] == "Legrand"


def embed_towards(index, product_id: int):
    idx = next(i for i, product in enumerate(index.products) if product["id"] == product_id)
    vector = index.embeddings[idx].copy()
    return lambda _query: vector


def test_exact_article_returns_price():
    index = tiny_index()
    result = search_products(
        "200300285_",
        limit=3,
        index=index,
        embed_query=embed_towards(index, 101),
    )
    top = result["results"][0]
    assert top["article"] == "200300285_"
    assert top["price"] == 64920
    assert top["exact_match"] is True


def test_specification_numbers_do_not_override_semantic_search():
    products = sample_products()
    products[0]["properties"]["ARTIKULPOSTAVSHCHIKA"] = "18"
    index = CatalogIndex(products, np.eye(len(products), dtype=np.float32))
    assert exact_matches(index, "светильник 18 Вт IP65") == []
    assert exact_matches(index, "автомат 101 А") == []
    assert exact_matches(index, "артикул 18")[0]["id"] == 101
    assert exact_matches(index, "101")[0]["id"] == 101
    assert exact_matches(index, "товар ID 101")[0]["id"] == 101
    result = search_products("светильник 18 Вт IP65", index=index,
                             embed_query=lambda _q: np.array([0, 0, 1], dtype=np.float32))
    assert result["results"] == []
    assert result["needs_clarification"] is True


def test_semantic_search_excludes_conflicting_current_rating():
    products = [
        {"id": 1, "name": "Автомат 16А", "article": "a1", "price": 10, "quantity": 2},
        {"id": 2, "name": "Автомат 160А", "article": "a2", "price": 20, "quantity": 2},
    ]
    index = CatalogIndex(products, np.array([[.8, .6], [1, 0]], dtype=np.float32))
    result = search_products("Автомат 16 А", index=index,
                             embed_query=lambda _q: np.array([1, 0], dtype=np.float32))
    assert [item["id"] for item in result["results"]] == [1]


def test_broad_search_keeps_unavailable_relevant_match():
    products = sample_products()
    embeddings = np.array([[0.99, 0.1, 0], [1, 0, 0], [0.8, 0.6, 0]], dtype=np.float32)
    index = CatalogIndex(products, embeddings)
    result = search_products("автомат", limit=2, index=index,
                             embed_query=lambda _q: np.array([1, 0, 0], dtype=np.float32))
    assert result["results"][0]["id"] == 102
    assert result["results"][0]["availability"] == "out_of_stock"
    assert any(item["id"] == 101 for item in result["results"][1:])
    assert all(item["id"] != 103 for item in result["results"])


def test_missing_requested_spec_is_marked_unverified():
    products = [
        {"id": 1, "name": "Автомат без указанного тока", "article": "a1", "price": 10, "quantity": 2},
        {"id": 2, "name": "Автомат 16А", "article": "a2", "price": 20, "quantity": 2},
    ]
    index = CatalogIndex(products, np.array([[1, 0], [.8, .6]], dtype=np.float32))
    result = search_products("Автомат 16 А", index=index,
                             embed_query=lambda _q: np.array([1, 0], dtype=np.float32))
    assert [item["id"] for item in result["results"]] == [2]
    products.pop()
    index = CatalogIndex(products, np.array([[1, 0]], dtype=np.float32))
    uncertain = search_products("Автомат 16 А", index=index,
                                embed_query=lambda _q: np.array([1, 0], dtype=np.float32))
    assert uncertain["results"][0]["unverified_specs"] == ["ток"]
    assert uncertain["needs_clarification"]


def test_cable_query_rejects_other_series_and_material():
    products = [
        {"id": 1, "name": "АВВГ 3х2,5", "article": "al", "price": 10, "quantity": 3,
         "category": "kabel_provod"},
        {"id": 2, "name": "ВВГ 3х2,5", "article": "cu", "price": 12, "quantity": 3,
         "category": "kabel_provod"},
        {"id": 3, "name": "КГ 3х2,5", "article": "kg", "price": 9, "quantity": 3,
         "category": "kabel_provod"},
    ]
    index = CatalogIndex(products, np.array([[1, 0], [.8, .6], [.9, .1]], dtype=np.float32))
    result = search_products("Кабель ВВГ 3х2.5", index=index,
                             embed_query=lambda _q: np.array([1, 0], dtype=np.float32))
    assert [item["id"] for item in result["results"]] == [2]


def test_lamp_wattage_and_breaker_type_are_not_inferred_from_similarity():
    lamps = [
        {"id": 1, "name": "Лампа E27 7W", "article": "l7", "price": 10, "quantity": 3},
        {"id": 2, "name": "Лампа E27 10W", "article": "l10", "price": 12, "quantity": 3},
    ]
    index = CatalogIndex(lamps, np.array([[1, 0], [.8, .6]], dtype=np.float32))
    result = search_products("лампа E27 10 Вт", index=index,
                             embed_query=lambda _q: np.array([1, 0], dtype=np.float32))
    assert [item["id"] for item in result["results"]] == [2]

    breakers = [
        {"id": 1, "name": "УЗО АВДТ 16А", "article": "rcbo", "price": 10, "quantity": 3},
        {"id": 2, "name": "Автоматический выключатель 16А", "article": "mcb", "price": 12, "quantity": 3},
    ]
    index = CatalogIndex(breakers, np.array([[1, 0], [.8, .6]], dtype=np.float32))
    result = search_products("автоматический выключатель 16 А", index=index,
                             embed_query=lambda _q: np.array([1, 0], dtype=np.float32))
    assert [item["id"] for item in result["results"]] == [2]


def test_exact_out_of_stock_article_returns_analog_with_reason():
    products = sample_products()
    embeddings = np.array(
        [
            [0.2, 0.1, 0.0, 0.0],
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
        ],
        dtype=np.float32,
    )
    index = CatalogIndex(products, embeddings)
    result = search_products(
        "200300000_",
        limit=3,
        index=index,
        embed_query=lambda _q: np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32),
    )
    assert result["results"][0]["id"] == 102
    analogs = [item for item in result["results"] if item.get("analog_of") == 102]
    assert analogs
    assert analogs[0]["id"] == 101
    assert "категория" in analogs[0]["analog_reason"]


def test_analog_requires_every_known_source_spec():
    products = [
        {"id": 1, "name": "УЗО 16А 2P", "article": "a1", "price": 10,
         "quantity": 0, "category": "uzo"},
        {"id": 2, "name": "УЗО 2P", "article": "a2", "price": 10,
         "quantity": 5, "category": "uzo"},
    ]
    index = CatalogIndex(products, np.array([[1, 0], [0, 1]], dtype=np.float32))
    result = search_products("a1", index=index)
    assert [item["id"] for item in result["results"]] == [1]


def test_final_constraints_preserve_explicit_multi_product_request():
    from app.search import constrain_results, product_hit

    index = tiny_index()
    products = [product_hit(index.get(pid)) for pid in (101, 103)]
    assert constrain_results("Кабель ВВГ 3х2,5 и автомат 160 А", products, index=index) == products


def test_structured_luminaire_type_prevents_rejecting_abbreviated_product_name():
    products = [{"id": 1, "name": "НПП 1101-100 бел/круг IEK", "quantity": 2,
                 "category": "svetilniki_lampy / svetilniki_dlya_vnutrennego_osveshcheniya",
                 "properties": {"TIP_SVETILNIKA": "С плафоном/рассеивателем"}}]
    index = CatalogIndex(products, np.array([[1, 0]], dtype=np.float32))
    result = search_products("Белый круглый светильник НПП 1101-100 IEK", index=index,
                             embed_query=lambda _q: np.array([1, 0], dtype=np.float32))
    assert [p["id"] for p in result["results"]] == [1]


def test_weak_semantic_match_is_rejected_and_threshold_override_is_evaluated():
    index = CatalogIndex([{"id": 1, "name": "Товар", "quantity": 1}],
                         np.array([[0.4, np.sqrt(1 - 0.4 ** 2)]], dtype=np.float32))
    embed = lambda _q: np.array([1, 0], dtype=np.float32)
    assert not search_products("нерелевантный запрос", index=index, embed_query=embed)["results"]
    assert search_products("нерелевантный запрос", index=index, embed_query=embed, min_score=.3)["results"]


def test_known_model_code_is_not_mistaken_for_missing_article():
    products = [{"id": 1, "name": "Перфоратор HB-20-24 КВТ", "article": "102589", "quantity": 2}]
    index = CatalogIndex(products, np.array([[1, 0]], dtype=np.float32))
    def unexpected(_query):
        raise AssertionError("A known model or missing article must not require embeddings")
    result = search_products("hb-20-24", index=index, embed_query=unexpected)
    assert [p["id"] for p in result["results"]] == [1]
    assert result["results"][0]["model_match"] is True
    assert "exact_match" not in result["results"][0]
    assert not search_products("артикул HB-20-24", index=index, embed_query=unexpected)["results"]
    assert not search_products("zzz-000-test", index=index, embed_query=unexpected)["results"]


def test_shared_model_code_requires_clarification_and_article_has_priority():
    products = [{"id": 1, "name": "Перфоратор HB-20-24 белый", "article": "a1", "quantity": 2},
                {"id": 2, "name": "Перфоратор HB-20-24 черный", "article": "a2", "quantity": 2}]
    index = CatalogIndex(products, np.eye(2, dtype=np.float32))
    result = search_products("HB-20-24", index=index)
    assert result["ambiguous"] and result["needs_clarification"]
    products[1]["article"] = "HB-20-24"
    index = CatalogIndex(products, np.eye(2, dtype=np.float32))
    result = search_products("HB-20-24", index=index)
    assert [p["id"] for p in result["results"]] == [2]
    assert result["results"][0]["exact_match"] is True
