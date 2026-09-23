function money(value) {
  if (value == null) return "—";
  return `${Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} ₸`;
}

let csrf = "";

function renderCart(cart) {
  const empty = document.getElementById("empty-cart");
  const sheet = document.getElementById("sheet");
  const rows = document.getElementById("rows");
  const grand = document.getElementById("grand");
  empty.hidden = Boolean(cart.items.length);
  sheet.hidden = !cart.items.length;
  grand.hidden = !cart.items.length;
  rows.replaceChildren();
  for (const item of cart.items) {
    const tr = document.createElement("tr");
    const name = document.createElement("td");
    name.textContent = item.name;
    const article = document.createElement("td");
    article.className = "mono";
    article.dataset.label = "Артикул";
    article.textContent = item.article || item.product_id;
    const qty = document.createElement("td");
    qty.className = "mono";
    qty.dataset.label = "Количество";
    qty.textContent = `${item.quantity} ${item.unit || "ед."}`;
    const price = document.createElement("td");
    price.className = "mono";
    price.dataset.label = "Цена";
    price.textContent = item.price_label || money(item.unit_price);
    const sum = document.createElement("td");
    sum.className = "mono";
    sum.dataset.label = "Сумма";
    sum.textContent = item.total_label || money(item.line_total);
    const action = document.createElement("td");
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "remove-line";
    remove.textContent = "Убрать";
    remove.setAttribute("aria-label", "Убрать из корзины: " + item.name);
    remove.addEventListener("click", () => removeLine(item.line_id));
    action.append(remove);
    tr.append(name, article, qty, price, sum, action);
    rows.appendChild(tr);
  }
  grand.textContent = `Итого ${cart.total_label || money(cart.total)}`;
}

function showStatus(message) {
  const status = document.getElementById("cart-status");
  status.textContent = message;
  status.hidden = !message;
}

async function removeLine(lineId) {
  if (!lineId) return;
  document.querySelectorAll(".remove-line").forEach(button => { button.disabled = true; });
  showStatus("Убираем товар…");
  try {
    const res = await fetch("/api/cart/remove", {
      method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify({ line_id: lineId })
    });
    const data = await res.json();
    if (!res.ok) { showStatus(data.error || "Не удалось убрать товар. Повторите действие."); return; }
    renderCart(data.cart);
    showStatus("Товар убран из корзины.");
  } catch { showStatus("Нет связи. Товар остался в корзине. Повторите действие."); }
  finally { document.querySelectorAll(".remove-line").forEach(button => { button.disabled = false; }); }
}

async function boot() {
  const res = await fetch("/api/state");
  if (!res.ok) throw new Error();
  const data = await res.json();
  csrf = data.csrf_token;
  renderCart(data.cart || { items: [], count: 0, total: 0 });
}

boot().catch(() => {
  document.getElementById("empty-cart").hidden = true;
  showStatus("Не удалось загрузить корзину. Обновите страницу.");
});
