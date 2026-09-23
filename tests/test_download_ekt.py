import asyncio

import download_ekt


def test_large_page_checkpoint_ignores_legacy_entries(tmp_path, monkeypatch):
    path = tmp_path / "list_checkpoint.txt"
    path.write_text("1\t1,2\n500:1\t1,2,3\n500:2\t4,5\n")
    monkeypatch.setattr(download_ekt, "LIST_PATH", path)

    assert download_ekt.load_list_pages() == {1: [1, 2, 3], 2: [4, 5]}


def test_collect_pages_uses_500_item_requests_and_one_detail_batch_per_page(tmp_path, monkeypatch):
    urls = []
    first_ids = list(range(1, 501))

    async def fake_get_json(_session, url, _sem, _timeout):
        urls.append(url)
        page = int(url.split("page=", 1)[1].split("&", 1)[0])
        ids = [501, 502, 503] if page == 2 else first_ids
        return {"per_page": 500, "items": [{"id": item_id} for item_id in ids]}

    monkeypatch.setattr(download_ekt, "get_json", fake_get_json)
    monkeypatch.setattr(download_ekt, "LIST_CONCURRENCY", 2)
    queue = asyncio.Queue()
    pages = {}
    scheduled = set()
    path = tmp_path / "list_checkpoint.txt"
    with path.open("a") as handle:
        asyncio.run(
            download_ekt.collect_pages(
                None, asyncio.Semaphore(2), pages, handle, queue, scheduled, {1, 2}
            )
        )

    assert pages == {1: first_ids, 2: [501, 502, 503]}
    assert queue.qsize() == 2
    assert [len(queue.get_nowait()[1]) for _ in range(2)] == [500, 3]
    assert all("per_page=500" in url for url in urls)
    assert path.read_text().startswith("500:1\t")


def test_download_details_commits_successful_page_once(monkeypatch, tmp_path):
    monkeypatch.setattr(download_ekt, "OUT", tmp_path)
    commits = []

    class FakeWriter:
        saved_ids = set()
        failed = 0

        def commit_page(self, number, rows):
            commits.append((number, rows))

    async def fake_get_json(_session, url, _sem):
        product_id = int(url.split("id=", 1)[1])
        if product_id == 500:
            raise RuntimeError("detail unavailable")
        return {"id": product_id, "name": f"Товар {product_id}"}

    monkeypatch.setattr(download_ekt, "CONCURRENCY", 1)
    monkeypatch.setattr(download_ekt, "get_json", fake_get_json)
    queue = asyncio.Queue()
    queue.put_nowait((1, list(range(1, 501))))
    queue.put_nowait(None)
    writer = FakeWriter()

    asyncio.run(download_ekt.download_details(None, asyncio.Semaphore(1), queue, writer))

    assert writer.failed == 1
    assert len(commits) == 1
    assert commits[0][0] == 1
    assert len(commits[0][1]) == 499
