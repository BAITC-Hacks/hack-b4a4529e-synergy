function money(value) {
  if (value == null) return "—";
  return `${Math.round(value).toLocaleString("ru-RU")} ₸`;
}

async function boot() {
  const res = await fetch("/api/state");
  const data = await res.json();
  const empty = document.getElementById("empty-cart");
  const sheet = document.getElementById("sheet");
  const rows = document.getElementById("rows");
  const grand = document.getElementById("grand");
  const cart = data.cart || { items: [], count: 0, total: 0 };

  if (!cart.items.length) {
    empty.hidden = false;
    sheet.hidden = true;
    grand.hidden = true;
    return;
  }

  empty.hidden = true;
  sheet.hidden = false;
  grand.hidden = false;
  rows.innerHTML = "";
  for (const item of cart.items) {
    const tr = document.createElement("tr");
    const name = document.createElement("td");
    name.textContent = item.name;
    const article = document.createElement("td");
    article.className = "mono";
    article.textContent = item.article || item.product_id;
    const qty = document.createElement("td");
    qty.className = "mono";
    qty.textContent = String(item.quantity);
    const price = document.createElement("td");
    price.className = "mono";
    price.textContent = money(item.unit_price);
    const sum = document.createElement("td");
    sum.className = "mono";
    sum.textContent = money(item.line_total);
    tr.append(name, article, qty, price, sum);
    rows.appendChild(tr);
  }
  grand.textContent = `Итого ${cart.total_label || money(cart.total)}`;
}

boot();
