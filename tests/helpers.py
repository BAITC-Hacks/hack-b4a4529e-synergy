import numpy as np

from app.search import CatalogIndex


def sample_products():
    return [
        {
            "id": 101,
            "name": "027228 АВ DRX250 MT 3ф 160А 18ka Legrand (1)",
            "article": "200300285_",
            "price": 64920,
            "quantity": 23,
            "image": None,
            "url": "https://ekt.kz/catalog/nizkovoltnaya_apparatura/silovye_avtomaticheskie_vyklyuchateli/item/",
            "category": "nizkovoltnaya_apparatura / silovye_avtomaticheskie_vyklyuchateli",
            "description": "Автоматический выключатель DRX250 MT 3P 160А 18kA",
            "properties": {"TORGOVAYA_MARKA": "Legrand", "NOMINALNYY_TOK": "160А"},
            "stores": [{"name": "Алматы", "quantity": 5}],
            "certificate": None,
            "spec_snippet": "Автоматический выключатель DRX250 MT 3P 160А 18kA",
        },
        {
            "id": 102,
            "name": "027228 АВ DRX250 MT 3ф 160А 18ka Legrand (нет)",
            "article": "200300000_",
            "price": 100,
            "quantity": 0,
            "image": None,
            "url": "https://ekt.kz/catalog/nizkovoltnaya_apparatura/silovye_avtomaticheskie_vyklyuchateli/gone/",
            "category": "nizkovoltnaya_apparatura / silovye_avtomaticheskie_vyklyuchateli",
            "description": "",
            "properties": {},
            "stores": [],
            "certificate": None,
            "spec_snippet": "",
        },
        {
            "id": 103,
            "name": "Кабель ВВГ 3х2.5",
            "article": "kab001",
            "price": 500,
            "quantity": 10,
            "image": None,
            "url": "https://ekt.kz/catalog/kabel/vvg/",
            "category": "kabel",
            "description": "",
            "properties": {},
            "stores": [],
            "certificate": None,
            "spec_snippet": "",
        },
    ]


def tiny_index(products=None) -> CatalogIndex:
    products = products or sample_products()
    n = len(products)
    embeddings = np.zeros((n, 4), dtype=np.float32)
    for i in range(n):
        embeddings[i, i % 4] = 1.0
    return CatalogIndex(products, embeddings)
