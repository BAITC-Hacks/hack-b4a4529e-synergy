const log = document.getElementById("log");
const welcome = document.getElementById("welcome");
const form = document.getElementById("composer");
const input = document.getElementById("message");
const sendBtn = document.getElementById("send");
const fileInput = document.getElementById("file");
const attachBtn = document.getElementById("attach");
const chips = document.getElementById("chips");
const statusEl = document.getElementById("status");
const cartCount = document.getElementById("cart-count");

let files = [];

function showStatus(text) {
  if (!text) {
    statusEl.hidden = true;
    statusEl.textContent = "";
    return;
  }
  statusEl.hidden = false;
  statusEl.textContent = text;
}

function updateCart(cart) {
  cartCount.textContent = cart && cart.count ? String(cart.count) : "0";
}

function addNode(node) {
  welcome.hidden = true;
  log.appendChild(node);
  node.scrollIntoView({ block: "end" });
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function renderUser(text, names) {
  const box = el("article", "bubble user");
  box.appendChild(el("p", "meta-line", "Вы"));
  if (text) box.appendChild(el("p", null, text));
  if (names && names.length) {
    box.appendChild(el("p", "meta-line", names.join(" · ")));
  }
  addNode(box);
}

function stockLabel(item) {
  if (item.availability === "unknown" || item.quantity == null) {
    return "остаток неизвестен";
  }
  if (item.quantity <= 0) return "нет в наличии";
  return `${item.quantity} шт`;
}

function money(value) {
  if (value == null) return "—";
  return `${Math.round(value).toLocaleString("ru-RU")} ₸`;
}

function renderHits(items) {
  if (!items || !items.length) return;
  const wrap = el("div", "hits");
  for (const item of items) {
    const row = el("article", "hit");
    if (item.image) {
      const img = el("img");
      img.src = item.image;
      img.alt = "";
      row.appendChild(img);
    } else {
      row.appendChild(el("div", "hit-ph", "ЭК"));
    }
    const body = el("div");
    body.appendChild(el("p", "article", item.article || `id ${item.id}`));
    body.appendChild(el("h3", null, item.name));
    if (item.analog_reason) {
      body.appendChild(el("p", "article", `аналог: ${item.analog_reason}`));
    }
    row.appendChild(body);
    const fig = el("p", "fig");
    fig.appendChild(document.createTextNode(money(item.price)));
    const stock = el("span", item.availability === "out_of_stock" ? "stock out" : "stock");
    stock.textContent = stockLabel(item);
    fig.appendChild(stock);
    row.appendChild(fig);
    wrap.appendChild(row);
  }
  addNode(wrap);
}

function renderProposal(proposal) {
  if (!proposal) return;
  const slip = el("aside", "slip");
  slip.dataset.proposal = "1";
  const stub = el("div", "stub");
  stub.appendChild(el("span", "stub-qty", String(proposal.quantity)));
  stub.appendChild(el("span", "stub-unit", "шт"));
  const body = el("div", "slip-body");
  body.appendChild(el("p", "slip-kicker", "К отгрузке"));
  body.appendChild(el("h2", null, proposal.name));
  body.appendChild(el("p", "article", proposal.article || ""));
  body.appendChild(
    el(
      "p",
      null,
      `${proposal.price_label || money(proposal.unit_price)} × ${proposal.quantity} = ${proposal.total_label || money(proposal.line_total)}`
    )
  );
  body.appendChild(el("p", "article", `в снимке ${proposal.stock} шт`));
  const actions = el("div", "actions");
  const cancel = el("button", "ghost", "Не добавлять");
  const confirm = el("button", "primary", "Добавить в корзину");
  cancel.type = "button";
  confirm.type = "button";
  cancel.addEventListener("click", async () => {
    const res = await fetch("/api/cart/cancel", { method: "POST" });
    const data = await res.json();
    updateCart(data.cart);
    slip.remove();
  });
  confirm.addEventListener("click", async () => {
    confirm.disabled = true;
    const res = await fetch("/api/cart/confirm", { method: "POST" });
    const data = await res.json();
    if (!res.ok) {
      showStatus(data.error || "Не удалось добавить");
      confirm.disabled = false;
      return;
    }
    updateCart(data.cart);
    slip.remove();
    renderAdded(data.cart);
  });
  actions.append(cancel, confirm);
  body.appendChild(actions);
  slip.append(stub, body);
  addNode(slip);
}

function renderAdded(cart) {
  const box = el("div", "added");
  const line = el("p");
  line.append(`В корзине ${cart.count} шт на ${cart.total_label}. `);
  const link = el("a", null, "Открыть корзину");
  link.href = "/cart";
  line.appendChild(link);
  box.appendChild(line);
  addNode(box);
}

function renderAssistant(text) {
  if (!text) return;
  const box = el("article", "bubble");
  box.appendChild(el("p", "meta-line", "Консультант"));
  box.appendChild(el("p", null, text));
  addNode(box);
}

function renderFiles() {
  chips.innerHTML = "";
  if (!files.length) {
    chips.hidden = true;
    return;
  }
  chips.hidden = false;
  for (const file of files) {
    chips.appendChild(el("li", null, file.name));
  }
}

attachBtn.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => {
  files = Array.from(fileInput.files || []);
  renderFiles();
});

input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 128)}px`;
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text && !files.length) return;
  showStatus("");
  renderUser(text, files.map((file) => file.name));
  const body = new FormData();
  body.append("message", text);
  for (const file of files) body.append("files", file);
  input.value = "";
  input.style.height = "auto";
  files = [];
  fileInput.value = "";
  renderFiles();
  sendBtn.disabled = true;
  showStatus("Ищем в каталоге…");
  try {
    const res = await fetch("/api/chat", { method: "POST", body });
    const data = await res.json();
    if (!res.ok) {
      showStatus(data.error || "Ошибка запроса");
      return;
    }
    showStatus("");
    updateCart(data.cart);
    renderHits(data.products);
    renderAssistant(data.text);
    renderProposal(data.proposal);
  } catch (err) {
    showStatus("Нет связи с сервером");
  } finally {
    sendBtn.disabled = false;
    input.focus();
  }
});

async function boot() {
  const res = await fetch("/api/state");
  const data = await res.json();
  updateCart(data.cart);
  if (data.proposal) renderProposal(data.proposal);
}

boot();
