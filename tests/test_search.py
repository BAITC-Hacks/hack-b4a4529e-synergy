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
    assert result["results"][0]["id"] == 2
    assert result["results"][1]["unverified_specs"] == ["ток"]


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
