import json
import zipfile
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


def test_single_file_index_bundle_loads_without_pointer(tmp_path):
    data, target = tmp_path / "data", tmp_path / "index"
    write_catalog(data, sample_products())
    folder = build_index(data, target, client=Obj(embeddings=Embeddings()))
    with zipfile.ZipFile(target / "catalog-index.zip", "w") as bundle:
        for name in ("metadata.json", "products.json", "embeddings.npy"):
            bundle.write(folder / name, name)
    (target / "current.json").unlink()
    assert CatalogIndex.load(target).get(101)["price"] == 64920


def test_empty_catalog_cannot_publish(tmp_path):
    with pytest.raises(ValueError, match="No products"):
        build_index(tmp_path, tmp_path / "index", client=Obj(embeddings=Embeddings()))


@pytest.mark.parametrize("version", [None, 999])
def test_outdated_embedding_template_cannot_load(tmp_path, version):
    data, target = tmp_path / "data", tmp_path / "index"
    write_catalog(data, sample_products())
    folder = build_index(data, target, client=Obj(embeddings=Embeddings()))
    path = folder / "metadata.json"
    metadata = json.loads(path.read_text())
    if version is None:
        metadata.pop("embedding_template_version")
    else:
        metadata["embedding_template_version"] = version
    path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="template changed"):
        CatalogIndex.load(target)


def test_stock_changes_reuse_vectors_but_refresh_product_metadata(tmp_path):
    data, target = tmp_path / "data", tmp_path / "index"
    products = sample_products()
    write_catalog(data, products)
    build_index(data, target, client=Obj(embeddings=Embeddings()))
    products[0].update(price=123, quantity=456)
    products[0]["properties"].update(KOL_VO_U_POSTAVSHCHIKA=789)
    write_catalog(data, products)
    build_index(data, target, client=Obj(embeddings=Embeddings(fail=True)))
    product = CatalogIndex.load(target).get(products[0]["id"])
    assert product["price"] == 123 and product["quantity"] == 456
    assert product["properties"]["KOL_VO_U_POSTAVSHCHIKA"] == 789
