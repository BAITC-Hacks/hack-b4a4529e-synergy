import json

import numpy as np

from app.catalog import category_from_url, load_products
from app.search import CatalogIndex, search_products
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


def test_out_of_stock_returns_analog_with_reason():
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
        "DRX250 которого нет",
        limit=3,
        index=index,
        embed_query=lambda _q: np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32),
    )
    assert result["results"][0]["id"] == 102
    analogs = [item for item in result["results"] if item.get("analog_of") == 102]
    assert analogs
    assert analogs[0]["id"] == 101
    assert "категория" in analogs[0]["analog_reason"]
