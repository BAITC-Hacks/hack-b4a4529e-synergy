import json
from types import SimpleNamespace as Obj

import pytest

from app.index_build import build_index
from app.search import CatalogIndex
from tests.helpers import sample_products


class Embeddings:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def create(self, *, model, input):
        self.calls += 1
        if self.fail:
            raise RuntimeError("simulated interruption")
        return Obj(data=[Obj(index=i, embedding=[1.0, float(i + 1), .5]) for i in range(len(input))])


def write_catalog(root, products):
    root.mkdir(exist_ok=True)
    (root / "products.json").write_text(json.dumps(products), encoding="utf-8")


def test_index_publish_cache_and_failure_preserves_active(tmp_path):
    data, target = tmp_path / "data", tmp_path / "index"
    products = sample_products()
    write_catalog(data, products)
    first = Embeddings()
    build_index(data, target, client=Obj(embeddings=first))
    active = (target / "current.json").read_text()
    assert first.calls == 1
    assert CatalogIndex.load(target).get(101)["price"] == 64920
    cached = Embeddings(fail=True)
    build_index(data, target, client=Obj(embeddings=cached))
    assert cached.calls == 0
    active = (target / "current.json").read_text()
    products[0]["name"] += " changed"
    write_catalog(data, products)
    with pytest.raises(RuntimeError):
        build_index(data, target, client=Obj(embeddings=Embeddings(fail=True)))
    assert (target / "current.json").read_text() == active
    assert CatalogIndex.load(target).get(101)["name"] == sample_products()[0]["name"]


def test_corrupted_products_cannot_load(tmp_path):
    data, target = tmp_path / "data", tmp_path / "index"
    write_catalog(data, sample_products())
    version = build_index(data, target, client=Obj(embeddings=Embeddings()))
    (version / "products.json").write_text("[]")
    with pytest.raises(ValueError, match="checksum"):
        CatalogIndex.load(target)


def test_empty_catalog_cannot_publish(tmp_path):
    with pytest.raises(ValueError, match="No products"):
        build_index(tmp_path, tmp_path / "index", client=Obj(embeddings=Embeddings()))
