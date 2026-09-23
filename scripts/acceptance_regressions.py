"""Targeted real HTTP checks after missing-article and attachment-review fixes."""
import argparse
import json

from app.attachments import validate_attachment
from app.cart import Session
from scripts import acceptance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--http-url", default="http://localhost:8000")
    args = parser.parse_args()
    acceptance.HTTP_URL = args.http_url
    rows = [acceptance.record_turn("missing-article", "Несуществующий артикул zzz-000-test", expect_empty=True),
            acceptance.record_turn("socket-query", "Розетка накладная")]
    data = (acceptance.OUT / "multi-specification.csv").read_bytes()
    session = Session("http-mixed-specification")
    rows.append(acceptance.record_turn("mixed-specification", "Разбери все четыре строки и сохрани их для выбора. Не добавляй товары.",
        session=session, files=[validate_attachment("multi-specification.csv", "text/csv", data)]))
    review = acceptance.HTTP_CLIENTS[session.id].get("/api/state").json()["attachment_review"]
    checks = {"all_rows": len(review) == 4,
              "statuses": sorted(r["status"] for r in review) == ["ambiguous", "resolved", "resolved", "unresolved"],
              "quantities": sorted(r["quantity"] for r in review) == [1, 2, 3, 5]}
    report = {"results": rows, "checks": checks, "passed": all(r["passed"] for r in rows) and all(checks.values())}
    (acceptance.OUT / "regressions.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": checks}))


if __name__ == "__main__":
    main()
