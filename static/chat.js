const log = document.getElementById("log");
const welcome = document.getElementById("welcome");
const form = document.getElementById("composer");
const input = document.getElementById("message");
const sendBtn = document.getElementById("send");
const fileInput = document.getElementById("file");
const attachBtn = document.getElementById("attach");
const chips = document.getElementById("chips");
const statusEl = document.getElementById("status");
const announcement = document.getElementById("announcement");
const cartCount = document.getElementById("cart-count");

document.querySelectorAll(".suggestion").forEach(button => {
  button.addEventListener("click", () => {
    input.value = button.firstChild.textContent.trim();
    resizeMessage();
    input.focus();
  });
});

const infoDialog = document.getElementById("info-dialog");
document.getElementById("open-info").addEventListener("click", () => infoDialog.showModal());

let files = [];
let csrf = "";
let proposal = null;
let busy = true;
let expiryTimer;
const draftQuantities = new Map();
const shownResults = new Map();
let currentState = { history: [], products: [], proposal: null, cart: { count: 0 } };

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function money(value) {
  return value == null ? "—" : Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 2 }) + " ₸";
}

function showStatus(text) {
  statusEl.textContent = text;
  statusEl.hidden = !text;
}

function setBusy(value) {
  busy = value;
  log.setAttribute("aria-busy", String(value));
  sendBtn.disabled = value;
  attachBtn.disabled = value;
  input.disabled = value;
  log.querySelectorAll("button").forEach(button => { button.disabled = value; });
  log.querySelectorAll("input").forEach(field => { field.disabled = value; });
  log.querySelectorAll("select").forEach(field => { field.disabled = value; });
}

function safeLink(label, value) {
  try {
    const url = new URL(value, location.origin);
    if (!["http:", "https:"].includes(url.protocol)) return null;
    const link = el("a", null, label);
    link.href = url.href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    return link;
  } catch { return null; }
}

function bubble(role, text) {
  const node = el("article", role === "user" ? "bubble user" : "bubble");
  node.append(el("p", "meta-line", role === "user" ? "Вы" : "Консультант"), el("p", "message-text", text));
  log.append(node);
  return node;
}

function renderHits(items) {
  if (!items?.length) return;
  const verified = items.filter(item => !item.unverified_specs?.length);
  const uncertain = items.filter(item => item.unverified_specs?.length);
  const section = el("section", "results");
  const heading = el("div", "results-heading");
  heading.append(el("h2", null, verified.length ? "Найдено в каталоге" : "Нужно уточнить характеристики"));
  if (verified.length) heading.append(el("span", "article", String(verified.length)));
  heading.append(snapshotDetails(currentState.snapshot));
  section.append(heading);
  if (verified.length) section.append(productList(verified));
  if (uncertain.length) {
    const review = el("details", "uncertain-results");
    review.open = !verified.length;
    review.append(el("summary", null, "Характеристики не подтверждены · " + uncertain.length),
      el("p", "results-note", "Проверьте характеристики у поставщика или уточните артикул в поиске."),
      productList(uncertain));
    section.append(review);
  }
  log.append(section);
}

function snapshotDetails(snapshot) {
  const details = el("details", "snapshot-note");
  details.append(el("summary", null, "О ценах и наличии"));
  const date = new Date(snapshot?.indexed_at);
  const stamp = snapshot?.indexed_at && !Number.isNaN(date.getTime())
    ? "Каталог собран " + date.toLocaleDateString("ru-RU") + ". " : "";
  details.append(el("p", null, stamp + "Цены и остатки — из снимка каталога. Дата обновления данных поставщиком не подтверждена."));
  return details;
}

function productList(items) {
  const list = el("div", "product-list");
  const wrap = el("div", "hits");
  let shown = 0;
  const key = items.map(item => item.id).join(",");
  const more = el("button", "show-more");
  more.type = "button";
  const count = el("p", "results-count");
  const footer = el("div", "results-footer");
  footer.append(count, more);
  const appendPage = (focus = false) => {
    const next = items.slice(shown, shown + (focus ? 5 : shownResults.get(key) || 5)).map(renderProduct);
    wrap.append(...next);
    shown += next.length;
    shownResults.set(key, shown);
    count.textContent = "Показано " + shown + " из " + items.length;
    more.textContent = "Показать ещё " + Math.min(5, items.length - shown);
    more.hidden = shown >= items.length;
    if (focus && next.length) {
      const title = next[0].querySelector("h3");
      title.tabIndex = -1;
      title.focus({ preventScroll: true });
      next[0].scrollIntoView({ block: "start" });
    }
  };
  more.addEventListener("click", () => appendPage(true));
  appendPage();
  list.append(wrap);
  if (items.length > 5) list.append(footer);
  return list;
}

function renderProduct(item) {
  const row = el("article", "hit");
  if (currentState.proposal?.items.some(line => line.product_id === item.id)) row.classList.add("is-selected");
  const thumb = el("div", "hit-thumb");
  if (item.image) {
    const image = el("img");
    image.alt = "";
    image.addEventListener("error", () => thumb.remove());
    thumb.append(image);
    image.src = item.image;
  }
  const body = el("div", "hit-main");
  const title = el("h3");
  const dimension = item.name.match(/\d+\s*[xх×]\s*\d+(?:[,.]\d+)?/i);
  if (dimension) {
    title.append(document.createTextNode(item.name.slice(0, dimension.index)),
      el("strong", "product-dimension", dimension[0].replace(/\s*[xх×]\s*/i, " × ")),
      document.createTextNode(item.name.slice(dimension.index + dimension[0].length)));
  } else title.textContent = item.name;
  body.append(title);
  if (item.analog_reason) body.append(el("p", "article", item.analog_reason));
  const summary = el("div", "hit-summary");
  const price = el("span", "price-group");
  price.append(el("strong", "hit-price", item.price_label || money(item.price)), el("span", "price-unit", " / " + (item.unit || "ед.")));
  summary.append(price);
  const options = item.purchase_options || { can_add: false, reason: "Обновите страницу для проверки количества." };
  const stock = item.quantity == null ? "Остаток неизвестен" : item.quantity <= 0 ? "Нет по снимку каталога"
    : "Остаток: " + Number(item.quantity).toLocaleString("ru-RU", { maximumFractionDigits: 6 }) + " " + (item.unit || "ед.");
  summary.append(el("span", item.quantity === 0 ? "stock out" : "stock", stock));
  body.append(summary);
  if (item.min_quantity) body.append(el("p", "article", "Минимальная партия: " + item.min_quantity + " " + item.unit));
  if (item.quantity_step) body.append(el("p", "article", "Кратность: " + item.quantity_step + " " + item.unit));
  const meta = el("div", "hit-meta");
  const details = el("details", "product-details");
  details.append(el("summary", null, "Подробнее"), el("p", null, "Артикул " + (item.article || "ID " + item.id)));
  if (item.source_observed_at) {
    const observed = new Date(item.source_observed_at);
    if (!Number.isNaN(observed.getTime()))
      details.append(el("p", null, "Данные товара получены " + observed.toLocaleString("ru-RU")));
  }
  if (item.purchase_rule_note) details.append(el("p", null,
    item.purchase_rule_note.startsWith("Исходное поле KRATNOST_MIN:")
      ? "Минимальную партию и кратность уточните у поставщика."
      : item.purchase_rule_note));
  if (item.description || item.spec_snippet) details.append(el("p", null, item.description || item.spec_snippet));
  for (const [key, value] of Object.entries(item.properties || {})) details.append(el("p", null, key + ": " + value));
  if (item.supplier_availability) details.append(el("p", "article", item.supplier_availability.note));
  for (const store of item.stores || []) {
    if (store.quantity != null && store.quantity > 0) details.append(el("p", null, store.name + ": " + store.quantity));
  }
  if (details.childElementCount > 1) meta.append(details);
  const links = el("div", "product-links");
  const certificates = [...new Set([item.certificate, ...(item.certificates || [])].filter(Boolean))];
  for (const [label, url] of [["Открыть на ekt.kz", item.url], ...certificates.map((url, i) => ["Сертификат " + (i + 1), url])]) {
    if (url) { const link = safeLink(label, url); if (link) links.append(link); }
  }
  if (links.childNodes.length) details.append(links);
  if (meta.childNodes.length) body.append(meta);
  if (item.unverified_specs?.length) {
    body.append(el("p", "cart-limit", "Не подтверждено: " + item.unverified_specs.join(", ") + "."));
  }
  if (options.can_add && !item.unverified_specs?.length) {
    const controls = el("div", "hit-actions");
    if (Number(options.existing)) controls.append(el("p", "in-cart", "В корзине: " + Number(options.existing).toLocaleString("ru-RU", { maximumFractionDigits: 6 }) + " " + (item.unit || "ед.")));
    const label = el("label", "quantity-label");
    label.append(el("span", "quantity-caption", "Количество"));
    const quantity = el("input", "quantity-input");
    quantity.type = "number";
    quantity.inputMode = "decimal";
    quantity.min = options.suggested_quantity;
    quantity.step = options.step;
    quantity.value = draftQuantities.get(item.id) || options.suggested_quantity;
    quantity.max = options.remaining;
    quantity.setAttribute("aria-label", "Количество для " + item.name);
    quantity.addEventListener("input", () => draftQuantities.set(item.id, quantity.value));
    label.append(quantity, el("span", "quantity-unit", item.unit || "ед."));
    const add = el("button", "add-product", "Выбрать");
    add.type = "button";
    add.setAttribute("aria-label", "Выбрать " + item.name);
    add.addEventListener("click", () => {
      const value = Number(quantity.value);
      if (!quantity.value || !Number.isFinite(value) || value <= 0) {
        quantity.focus();
        showStatus("Укажите количество больше нуля.");
        return;
      }
      if (quantity.validity.rangeOverflow) {
        quantity.focus();
        showStatus("Доступно для добавления: " + options.remaining + " " + (item.unit || "ед."));
        return;
      }
      if (!quantity.checkValidity()) {
        quantity.focus();
        showStatus("Проверьте количество товара.");
        return;
      }
      prepareProduct(item.id, quantity.value);
    });
    controls.append(label, add);
    if (item.image) row.append(thumb);
    row.append(body, controls);
  } else {
    if (item.image) row.append(thumb);
    row.append(body);
    if (options.reason && !item.unverified_specs?.length && !(item.quantity != null && Number(item.quantity) <= 0))
      body.append(el("p", "cart-limit", options.reason));
  }
  return row;
}

async function prepareProduct(productId, quantity) {
  if (busy) return;
  setBusy(true);
  showStatus("Проверяем количество и стоимость…");
  try {
    const res = await fetch("/api/cart/propose", {
      method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify({ product_id: productId, quantity })
    });
    const data = await res.json();
    if (data.cart) renderState(data, { focus: res.ok ? "proposal" : "input" });
    showStatus(res.ok ? "" : data.error || (res.status === 404
      ? "Обновите страницу и повторите действие."
      : res.status === 422 ? "Проверьте количество и повторите действие."
      : "Не удалось подготовить товар. Повторите действие."));
  } catch { showStatus("Нет связи. Попробуйте ещё раз."); }
  finally { setBusy(false); }
}

function renderProposal(value) {
  proposal = value;
  clearTimeout(expiryTimer);
  if (!value) return;
  const remaining = value.expires_at * 1000 - Date.now();
  if (remaining <= 0) { proposal = null; return; }
  const slip = el("aside", "slip");
  slip.dataset.proposal = value.id;
  slip.setAttribute("aria-labelledby", "proposal-heading");
  const body = el("div", "slip-body");
  const heading = el("h2", null, "Проверьте выбор");
  heading.id = "proposal-heading";
  heading.tabIndex = -1;
  body.append(heading);
  const lines = el("div", "proposal-lines");
  for (const item of value.items) {
    const line = el("div", "proposal-line");
    line.append(el("p", "proposal-name", item.name), el("p", "article",
      Number(item.quantity).toLocaleString("ru-RU", { maximumFractionDigits: 6 }) + " " + item.unit + " × " + item.price_label), el("strong", "proposal-price", item.total_label));
    lines.append(line);
  }
  body.append(lines);
  const footer = el("div", "proposal-footer");
  const total = el("p", "proposal-total");
  total.append(el("span", null, "Итого"), el("strong", null, value.total_label));
  footer.append(total);
  const actions = el("div", "actions");
  for (const [name, label, className] of [["cancel", "Отмена", "ghost"], ["confirm", "Добавить в корзину", "primary"]]) {
    const button = el("button", className, label);
    button.type = "button";
    button.addEventListener("click", () => changeCart(name, value.id));
    actions.append(button);
  }
  footer.append(actions);
  body.append(footer);
  slip.append(body);
  log.append(slip);
  expiryTimer = setTimeout(() => {
    const hadFocus = slip.contains(document.activeElement);
    slip.remove();
    if (proposal?.id === value.id) proposal = null;
    currentState.proposal = null;
    log.querySelectorAll(".is-selected").forEach(row => row.classList.remove("is-selected"));
    showStatus("Предложение истекло. Попросите подготовить его снова.");
    if (hadFocus) input.focus();
  }, remaining);
}

function renderState(data, { focus } = {}) {
  currentState = { ...currentState, ...data };
  if (["added", "already_added"].includes(data.status)) draftQuantities.clear();
  for (const item of currentState.proposal?.items || []) {
    if (!draftQuantities.has(item.product_id)) draftQuantities.set(item.product_id, String(item.quantity));
  }
  if (data.csrf_token) csrf = data.csrf_token;
  cartCount.textContent = String(currentState.cart?.items?.length || 0);
  cartCount.hidden = !currentState.cart?.items?.length;
  log.replaceChildren();
  if (!currentState.history?.length && !currentState.products?.length && !currentState.proposal) log.append(welcome);
  for (const item of currentState.history || []) bubble(item.role, item.content);
  renderConsultation(currentState);
  renderHits(currentState.products);
  renderProposal(currentState.proposal);
  if (["added", "already_added"].includes(data.status)) {
    const added = el("div", "added");
    added.setAttribute("role", "status");
    added.tabIndex = -1;
    added.append(el("span", null, "Добавлено в корзину"));
    const link = el("a", null, "Открыть корзину");
    link.href = "/cart";
    added.append(link);
    log.append(added);
  }
  setBusy(busy);
  const replies = log.querySelectorAll(".bubble:not(.user)");
  const target = log.querySelector(".added") || log.querySelector(".slip") || replies[replies.length - 1]
    || log.querySelector(".spec-review") || log.querySelector(".results")
    || log.lastElementChild;
  if (target && target !== welcome) target.scrollIntoView({ block: "start" });
  if (focus === "proposal") document.getElementById("proposal-heading")?.focus({ preventScroll: true });
  if (focus === "input") requestAnimationFrame(() => input.focus({ preventScroll: true }));
  if (focus === "result") requestAnimationFrame(() => (log.querySelector(".added") || input).focus({ preventScroll: true }));
}

function renderConsultation(state) {
  const sources = el("div", "consultation-sources");
  for (const source of state.sources || []) {
    const link = safeLink(source.title || "Источник", source.url);
    if (link) sources.append(link);
  }
  if (sources.childNodes.length) log.append(sources);
  if (state.snapshot?.indexed_at && !state.products?.length) log.append(snapshotDetails(state.snapshot));
  for (const issue of state.attachment_issues || []) log.append(el("p", "attachment-issue", issue));
  if (!state.attachment_review?.length) return;
  const review = el("section", "spec-review");
  review.append(el("h2", null, "Разбор спецификации"), el("p", "article", "Выберите позиции и проверьте количество. До 50 строк в одном предложении."));
  const choices = [];
  const labels = { resolved: "Найден по артикулу", ambiguous: "Выберите совпадение", unresolved: "Не найден — уточните в чате", quantity_required: "Укажите количество" };
  state.attachment_review.forEach((item, index) => {
    const row = el("div", "spec-row");
    row.append(el("p", "article", item.filename + " · " + item.source_reference),
      el("p", null, item.query || "Нечитаемая строка"), el("p", "article", labels[item.status] || item.status));
    if (item.candidates?.length) {
      const include = el("input"); include.type = "checkbox";
      include.setAttribute("aria-label", "Выбрать строку " + (index + 1));
      const select = el("select"); select.setAttribute("aria-label", "Товар для строки " + (index + 1));
      select.append(new Option("Выберите товар", ""));
      for (const product of item.candidates) {
        const option = new Option((product.article || product.id) + " · " + product.name, String(product.id));
        option.disabled = product.quantity == null || product.quantity <= 0 || product.price == null;
        select.append(option);
      }
      if (item.status === "resolved" && !select.options[1].disabled) select.value = String(item.candidates[0].id);
      const quantity = el("input", "quantity-input");
      quantity.type = "number"; quantity.min = "0.000001"; quantity.step = "any";
      quantity.value = item.quantity == null ? "" : String(item.quantity);
      quantity.setAttribute("aria-label", "Количество для строки " + (index + 1));
      const controls = el("div", "spec-controls");
      const includeLabel = el("label", "spec-choice", "Выбрать "); includeLabel.prepend(include);
      controls.append(includeLabel, select, quantity); row.append(controls);
      choices.push({ include, select, quantity });
    }
    review.append(row);
  });
  const prepare = el("button", "primary", "Подготовить выбранные"); prepare.type = "button";
  prepare.addEventListener("click", async () => {
    if (busy) return;
    const selected = choices.filter(c => c.include.checked);
    if (!selected.length || selected.length > 50) { showStatus("Выберите от 1 до 50 строк."); return; }
    if (selected.some(c => !c.select.value || !c.quantity.value || !c.quantity.checkValidity())) {
      showStatus("Выберите товар и положительное количество для каждой выбранной строки."); return;
    }
    setBusy(true);
    try {
      const res = await fetch("/api/cart/propose-items", {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
        body: JSON.stringify({ items: selected.map(c => ({ product_id: Number(c.select.value), quantity: c.quantity.value })) })
      });
      const data = await res.json();
      if (data.cart) renderState(data, { focus: res.ok ? "proposal" : "input" });
      showStatus(res.ok ? "" : data.error || "Не удалось подготовить выбор. Проверьте количество.");
    } catch { showStatus("Нет связи. Попробуйте ещё раз."); }
    finally { setBusy(false); }
  });
  review.append(prepare); log.append(review);
}

async function changeCart(action, id) {
  if (busy) return;
  setBusy(true);
  showStatus("");
  try {
    const res = await fetch("/api/cart/" + action, {
      method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify({ proposal_id: id })
    });
    const data = await res.json();
    if (data.cart) renderState(data, { focus: res.ok ? "result" : "input" });
    if (!res.ok) showStatus(data.error || "Не удалось изменить корзину.");
  } catch { showStatus("Нет связи. Повторите действие: товар не добавится дважды."); }
  finally { setBusy(false); }
}

function renderFiles() {
  chips.replaceChildren();
  chips.hidden = !files.length;
  files.forEach((file, index) => {
    const li = el("li", null, file.name + " ");
    const remove = el("button", "remove-file", "Убрать");
    remove.type = "button";
    remove.addEventListener("click", () => { files.splice(index, 1); renderFiles(); });
    li.append(remove);
    chips.append(li);
  });
}
attachBtn.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => {
  const selected = Array.from(fileInput.files || []);
  const next = [...files, ...selected];
  fileInput.value = "";
  if (next.length > 5 || next.some(f => f.size > 10 * 1024 * 1024) || next.reduce((n, f) => n + f.size, 0) > 20 * 1024 * 1024) {
    showStatus("До 5 файлов: 10 МБ на файл, 20 МБ суммарно.");
    return;
  }
  files = next;
  showStatus("");
  renderFiles();
});
function resizeMessage() {
  input.style.height = "auto";
  input.style.overflowY = input.scrollHeight > 128 ? "auto" : "hidden";
  input.style.height = Math.min(input.scrollHeight, 128) + "px";
}
input.addEventListener("input", resizeMessage);
input.addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); form.requestSubmit(); }
});
form.addEventListener("submit", async event => {
  event.preventDefault();
  if (busy) return;
  const text = input.value.trim();
  if (!text && !files.length) return;
  const body = new FormData();
  body.append("message", text);
  body.append("proposal_id", proposal?.id || "");
  for (const file of files) body.append("files", file);
  setBusy(true);
  input.blur();
  showStatus("");
  welcome.remove();
  bubble("user", text || "Посмотрите вложение.");
  const pending = bubble("assistant", "Ищем ответ…");
  pending.classList.add("pending-reply");
  pending.scrollIntoView({ block: "end" });
  announcement.textContent = "Ищем ответ…";
  try {
    const res = await fetch("/api/chat", { method: "POST", headers: { "X-CSRF-Token": csrf }, body });
    const data = await res.json();
    if (!res.ok) {
      renderState(currentState);
      showStatus(data.error || data.detail || "Не удалось получить ответ.");
      return;
    }
    input.value = "";
    resizeMessage();
    files = [];
    fileInput.value = "";
    renderFiles();
    shownResults.clear();
    renderState(data);
    announcement.textContent = data.text || "Ответ готов.";
    showStatus(data.ok === false ? data.error : "");
  } catch {
    renderState(currentState);
    showStatus("Нет связи с сервером. Сообщение сохранено в поле ввода.");
  }
  finally { setBusy(false); }
});

async function boot() {
  setBusy(true);
  try {
    const res = await fetch("/api/state");
    if (!res.ok) throw new Error();
    renderState(await res.json());
    setBusy(false);
  } catch { showStatus("Не удалось загрузить чат. Обновите страницу."); }
}
boot();
