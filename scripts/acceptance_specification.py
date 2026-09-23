"""Real-model multi-line attachment acceptance; synthetic request, real catalog."""
import json

from app.agent import run_turn
from app.attachments import validate_attachment
from app.cart import Session, number
from app.config import CHAT_MODEL
from app.search import get_index
from scripts.acceptance import OUT, record_turn


def main():
    index = get_index()
    products = [p for p in index.products if number(p.get("price")) > 0 and number(p.get("quantity")) >= 3 and p.get("article")][:2]
    content = ("SKU or description,Quantity\n" +
               f"{products[0]['article']},2\n{products[1]['article']},3\n" +
               "ZZZ-NONEXISTENT-ACCEPTANCE-12345,1\nКабель,5\n").encode()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "multi-specification.csv").write_bytes(content)
    session = Session("multi-specification-acceptance")
    upload = validate_attachment("multi-specification.csv", "text/csv", content)
    result = record_turn("multi-specification", "Разбери все четыре строки, включая ненайденные и неоднозначные. Не добавляй товары.",
                         session=session, files=[upload], expected_id=products[0]["id"])
    rows = session.attachment_review
    checks = {"all_four_rows": len(rows) == 4,
              "two_resolved": sum(r["status"] == "resolved" for r in rows) == 2,
              "unresolved": any(r["status"] == "unresolved" for r in rows),
              "ambiguous": any(r["status"] == "ambiguous" for r in rows),
              "source_references": all(r["filename"] == "multi-specification.csv" and r["source_reference"] != "не указано" for r in rows),
              "quantities": sorted(r["quantity"] for r in rows) == [1, 2, 3, 5],
              "cart_unchanged": not session.cart and session.pending is None}
    follow_up = run_turn(session, "Подготовь к добавлению только первые две найденные строки спецификации с указанными там количествами.")
    pending = session.pending
    checks["follow_up_quantities"] = bool(pending) and sorted((line.product_id, number(line.quantity)) for line in pending.items) == sorted((p["id"], number(q)) for p, q in zip(products, (2, 3)))
    checks["follow_up_still_requires_confirmation"] = not session.cart and bool(follow_up.get("proposal"))
    report = {"model": CHAT_MODEL, "snapshot": index.metadata["version"], "upload": result,
              "checks": checks, "passed": result["passed"] and all(checks.values())}
    (OUT / "multi.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": checks}))


if __name__ == "__main__":
    main()
