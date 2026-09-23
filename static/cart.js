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
  document.getElementById("cart-content").hidden = !cart.items.length;
  document.getElementById("cart-caption").textContent = cart.items.length
    ? "Выбранные товары · " + cart.items.length.toLocaleString("ru-RU")
    : "Всё нужное для вашей задачи — в одном месте.";
  rows.replaceChildren();
  for (const item of cart.items) {
    const tr = document.createElement("tr");
    const name = document.createElement("td");
    const title = document.createElement("p");
    title.className = "cart-product-name";
    title.textContent = item.name;
    const article = document.createElement("p");
    article.className = "article";
    article.textContent = "Артикул " + (item.article || item.product_id);
    name.append(title, article);
    const qty = document.createElement("td");
    qty.className = "mono";
    qty.dataset.label = "Количество";
    const quantity = document.createElement("input");
    quantity.type = "number"; quantity.min = "0.000001"; quantity.step = "any";
    quantity.className = "quantity-input"; quantity.value = item.quantity;
    quantity.setAttribute("aria-label", "Количество в корзине для " + item.name);
    const save = document.createElement("button");
    save.type = "button"; save.className = "text-button"; save.textContent = "Сохранить";
    save.setAttribute("aria-label", "Сохранить количество для " + item.name);
    save.addEventListener("click", () => {
      if (!quantity.value || !quantity.reportValidity()) return;
      saveQuantity(item.line_id, quantity.value);
    });
    qty.append(quantity, document.createTextNode(" " + (item.unit || "ед.")), save);
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
    remove.textContent = "Удалить";
    remove.setAttribute("aria-label", "Убрать из корзины: " + item.name);
    remove.addEventListener("click", () => removeLine(item.line_id));
    action.append(remove);
    tr.append(name, qty, price, sum, action);
    rows.appendChild(tr);
  }
  grand.textContent = cart.total_label || money(cart.total);
}

function renderProposal(proposal) {
  const section = document.getElementById("cart-proposal");
  section.replaceChildren(); section.hidden = !proposal;
  if (!proposal) return;
  const heading = document.createElement("h2"); heading.textContent = "Подтвердите добавление";
  const note = document.createElement("p"); note.textContent = "Текущее количество ещё не изменилось. Будет добавлено:";
  section.append(heading, note);
  for (const item of proposal.items) {
    const line = document.createElement("p");
    line.textContent = `${item.name} — ещё ${item.quantity} ${item.unit}, ${item.total_label}`;
    section.append(line);
  }
  const total = document.createElement("strong"); total.textContent = "Стоимость добавления: " + proposal.total_label;
  section.append(total);
  const actions = document.createElement("div"); actions.className = "actions";
  for (const [action, label] of [["cancel", "Отмена"], ["confirm", "Подтвердить добавление"]]) {
    const button = document.createElement("button"); button.type = "button";
    button.className = action === "confirm" ? "primary" : "ghost"; button.textContent = label;
    button.addEventListener("click", () => cartRequest("/api/cart/" + action, {proposal_id: proposal.id}));
    actions.append(button);
  }
  section.append(actions);
  section.tabIndex = -1; section.focus();
}

let updating = false;
async function cartRequest(url, body) {
  if (updating) return;
  updating = true;
  document.querySelectorAll("main button").forEach(button => { button.disabled = true; });
  try {
    const response = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf}, body: JSON.stringify(body)});
    const data = await response.json();
    if (data.cart) { renderCart(data.cart); renderProposal(data.proposal); }
    showStatus(!response.ok ? data.error || "Не удалось изменить количество." : data.proposal
      ? "Проверьте дополнительное количество и подтвердите добавление." : data.status === "cancelled"
      ? "Добавление отменено. Количество не изменилось." : "Корзина обновлена.");
  } catch { showStatus("Нет связи. Проверьте корзину после восстановления соединения."); }
  finally {
    updating = false;
    document.querySelectorAll("main button").forEach(button => { button.disabled = false; });
  }
}

function saveQuantity(lineId, quantity) { return cartRequest("/api/cart/quantity", {line_id: lineId, quantity}); }

function showStatus(message) {
  const status = document.getElementById("cart-status");
  status.textContent = message;
  status.hidden = !message;
}

async function removeLine(lineId) {
  if (!lineId || updating) return;
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
    renderProposal(null);
    showStatus("Товар убран из корзины.");
    (document.querySelector(".remove-line") || document.querySelector("#empty-cart .cart-return")).focus();
  } catch { showStatus("Нет связи. Товар остался в корзине. Повторите действие."); }
  finally { document.querySelectorAll(".remove-line").forEach(button => { button.disabled = false; }); }
}

async function boot() {
  const res = await fetch("/api/state");
  if (!res.ok) throw new Error();
  const data = await res.json();
  csrf = data.csrf_token;
  renderCart(data.cart || { items: [], count: 0, total: 0 });
  renderProposal(data.proposal);
}

boot().catch(() => {
  document.getElementById("empty-cart").hidden = true;
  document.getElementById("cart-caption").textContent = "Не удалось загрузить товары.";
  showStatus("Не удалось загрузить корзину. Обновите страницу.");
});
