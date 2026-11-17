/* TicketFlash · interfaz web.
   Consume la API REST (FastAPI), que a su vez usa redis-py contra Redis Cloud. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const HOLD_TTL = 600; // segundos (debe coincidir con HOLD_TTL_SECONDS)

  const state = {
    token: sessionStorage.getItem("tf_token"), user: null, sessionTtl: 0,
    users: [], events: [], holds: [], orders: [], ranking: [],
    category: "", query: "", current: null, qty: 1,
  };

  // ------------------------------------------------------------- utilidades
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = (n) => Number(n).toLocaleString("es", { style: "currency", currency: "EUR" });
  const mmss = (s) => `${String(Math.max(0, Math.floor(s / 60))).padStart(2, "0")}:${String(Math.max(0, s % 60)).padStart(2, "0")}`;
  const initials = (name) => name.split(/\s+/).map((p) => p[0]).slice(0, 2).join("").toUpperCase();
  const dateParts = (iso) => {
    const d = new Date(iso);
    if (isNaN(d)) return { day: "--", mon: "", long: iso };
    return {
      day: d.toLocaleDateString("es", { day: "2-digit" }),
      mon: d.toLocaleDateString("es", { month: "short" }).replace(".", ""),
      long: d.toLocaleString("es", { weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" }),
    };
  };
  const lastRender = {};
  const changed = (key, data) => { const j = JSON.stringify(data); if (lastRender[key] === j) return false; lastRender[key] = j; return true; };

  // Pósters generados: degradado + emoji por categoría (sin imágenes externas).
  const THEMES = {
    musica: ["#ff2e63", "#6a00f4", "🎸"], deporte: ["#00c896", "#0041ff", "🏀"], teatro: ["#ff9f1c", "#d7263d", "🎭"],
    comedia: ["#ffd60a", "#ff5400", "🎤"], tecnologia: ["#00d4ff", "#2b2d9e", "💻"], cine: ["#f72585", "#3a0ca3", "🎬"],
    familia: ["#80ed99", "#22577a", "🎈"], arte: ["#ff70a6", "#ff9770", "🎨"],
  };
  function theme(e) {
    const t = THEMES[(e.category || "").toLowerCase()];
    if (t) return t;
    let h = 0; for (const c of e.category || e.name) h = (h * 31 + c.charCodeAt(0)) % 360;
    return [`hsl(${h} 85% 58%)`, `hsl(${(h + 60) % 360} 80% 35%)`, "🎟️"];
  }
  function posterHTML(e, { withTitle = true } = {}) {
    const [a, b, emoji] = theme(e);
    const angle = (parseInt(e.id, 10) * 47) % 360;
    return `<div class="poster-bg" style="background:linear-gradient(${angle}deg, ${a}, ${b})"></div>
            <div class="poster-grain"></div>
            <span class="poster-emoji">${emoji}</span>
            ${withTitle ? `<div class="poster-title">${esc(e.name)}</div>` : ""}`;
  }

  // -------------------------------------------------------------------- API
  async function api(path, { method = "GET", body, auth = false } = {}) {
    const headers = { "Content-Type": "application/json" };
    if (auth && state.token) headers["X-Session-Token"] = state.token;
    const res = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
    if (res.status === 204) return null;
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      if (res.status === 401 && auth) endSession("Tu sesión caducó en Redis (TTL agotado).");
      const d = data.detail;
      throw new Error(typeof d === "string" ? d : Array.isArray(d) ? d.map((x) => x.msg).join(", ") : res.statusText);
    }
    return data;
  }

  function toast(msg, kind = "") {
    const el = document.createElement("div");
    el.className = `toast ${kind}`; el.textContent = msg;
    $("toasts").appendChild(el);
    setTimeout(() => el.remove(), 3800);
  }

  // ------------------------------------------------------------ modales/UI
  const open = (id) => $(id).classList.remove("hidden");
  const close = (id) => $(id).classList.add("hidden");
  document.querySelectorAll(".overlay").forEach((o) => {
    o.addEventListener("click", (e) => { if (e.target === o || e.target.hasAttribute("data-close")) o.classList.add("hidden"); });
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    document.querySelectorAll(".overlay").forEach((o) => o.classList.add("hidden"));
    toggleDrawer(false);
  });
  function toggleDrawer(show) {
    $("drawer").classList.toggle("open", show);
    $("scrim").classList.toggle("hidden", !show);
  }

  // ----------------------------------------------------------------- sesión
  async function loadUsers() {
    state.users = await api("/users");
    $("user-list").innerHTML = state.users.map((u) => `
      <button class="user-pick" data-id="${esc(u.id)}">
        <span class="av">${esc(initials(u.name))}</span>
        <span><b>${esc(u.name)}</b><small>${esc(u.email)}</small></span>
      </button>`).join("") || '<p class="muted">No hay perfiles todavía.</p>';
  }

  async function login(userId) {
    const s = await api("/auth/login", { method: "POST", body: { user_id: userId } });
    state.token = s.token; state.sessionTtl = s.ttl_seconds;
    sessionStorage.setItem("tf_token", s.token);
    close("login-modal");
    await restoreSession();
    toast(`Hola, ${state.user.name.split(" ")[0]}. Sesión creada en Redis (TTL ${s.ttl_seconds / 60} min)`, "ok");
  }

  async function restoreSession() {
    if (!state.token) return renderSession();
    try {
      const me = await api("/auth/me", { auth: true });
      state.user = me.user; state.sessionTtl = me.session_ttl;
    } catch (_) { state.token = null; sessionStorage.removeItem("tf_token"); }
    renderSession();
    await refreshMine();
  }

  function renderSession() {
    const logged = !!state.user;
    $("login-open").classList.toggle("hidden", logged);
    $("hero-login").classList.toggle("hidden", logged);
    $("avatar-btn").classList.toggle("hidden", !logged);
    $("session-card").classList.toggle("hidden", !logged);
    if (logged) {
      $("avatar-btn").textContent = initials(state.user.name);
      $("avatar-btn").title = `${state.user.name} · abrir mis entradas`;
      $("drawer-user").textContent = `${state.user.name} · ${state.user.email}`;
      $("session-ttl").textContent = mmss(state.sessionTtl);
    } else {
      $("drawer-user").textContent = "Inicia sesión para ver tus reservas.";
      $("holds").innerHTML = ""; $("orders").innerHTML = "";
      $("bag-count").classList.add("hidden");
      delete lastRender.holds; delete lastRender.orders;
    }
    if (state.current) renderModal();
  }

  function endSession(msg) {
    state.token = null; state.user = null; state.holds = []; state.orders = [];
    sessionStorage.removeItem("tf_token");
    renderSession();
    if (msg) toast(msg);
  }

  async function logout() {
    try { await api("/auth/logout", { method: "POST", auth: true }); } catch (_) { /* ya caducada */ }
    endSession("Sesión cerrada: la clave se eliminó de Redis.");
    toggleDrawer(false);
  }

  // ---------------------------------------------------------------- eventos
  function filtered() {
    const q = state.query.trim().toLowerCase();
    return state.events.filter((e) =>
      (!state.category || e.category === state.category) &&
      (!q || `${e.name} ${e.venue} ${e.category}`.toLowerCase().includes(q)));
  }

  function renderChips() {
    const counts = {};
    state.events.forEach((e) => { counts[e.category] = (counts[e.category] || 0) + 1; });
    const cats = Object.keys(counts).sort();
    if (!changed("chips", [counts, state.category])) return;
    $("chips").innerHTML = [`<button class="chip ${state.category ? "" : "active"}" data-cat="">Todo<span class="count">${state.events.length}</span></button>`]
      .concat(cats.map((c) => `<button class="chip ${state.category === c ? "active" : ""}" data-cat="${esc(c)}">${esc(c)}<span class="count">${counts[c]}</span></button>`))
      .join("");
    $("cat-list").innerHTML = cats.map((c) => `<option value="${esc(c)}">`).join("");
  }

  function renderGrid() {
    const list = filtered();
    $("result-count").textContent = `${list.length} ${list.length === 1 ? "evento" : "eventos"}`;
    if (!changed("grid", [list, state.query])) return;
    if (!list.length) {
      $("grid").innerHTML = `<div class="empty">${state.events.length ? "Ningún evento coincide con tu búsqueda." : "No hay eventos. Pulsa ＋ para crear uno o ejecuta <code>scripts/seed.py</code>."}</div>`;
      return;
    }
    $("grid").innerHTML = list.map((e) => {
      const d = dateParts(e.date);
      const pct = e.capacity ? e.stock / e.capacity : 0;
      const out = e.stock === 0, low = !out && (e.stock <= 10 || pct < 0.15);
      const flag = out ? '<span class="flag out">Agotado</span>' : low ? '<span class="flag hot">Últimas</span>' : e.sold >= 5 ? '<span class="flag">🔥 Popular</span>' : "";
      return `
      <button class="card" data-id="${esc(e.id)}">
        <div class="poster ${out ? "soldout" : ""}">
          ${posterHTML(e)}
          <div class="date-badge"><b>${d.day}</b><small>${d.mon}</small></div>
          ${flag}
        </div>
        <div class="card-info">
          <h3>${esc(e.name)}</h3>
          <span class="where">${esc(e.venue)}</span>
          <div class="card-foot">
            <span class="price"><small>desde</small>${money(e.price)}</span>
            <span class="left ${low ? "low" : ""}">${out ? "Sin entradas" : `Quedan ${e.stock}`}</span>
          </div>
          <div class="mini-meter"><i style="width:${Math.round((1 - pct) * 100)}%"></i></div>
        </div>
      </button>`;
    }).join("");
  }

  function renderTop() {
    if (!changed("top", [state.ranking, state.events.map((e) => [e.id, e.category])])) return;
    if (!state.ranking.length) { $("top-row").innerHTML = '<p class="muted">Aún no hay ventas.</p>'; return; }
    $("top-row").innerHTML = state.ranking.map((r, i) => {
      const e = state.events.find((x) => x.id === r.event_id) || { id: r.event_id, name: r.name, category: "" };
      const [a, b, emoji] = theme(e);
      return `
      <button class="top-card" data-id="${esc(r.event_id)}">
        <span class="rank">${i + 1}</span>
        <span class="mini-poster" style="background:linear-gradient(135deg, ${a}, ${b})">${emoji}</span>
        <span><b>${esc(r.name)}</b><small>${r.sold} entradas vendidas</small></span>
      </button>`;
    }).join("");
  }

  function renderStats(keys, health) {
    const sold = state.events.reduce((s, e) => s + e.sold, 0);
    const stock = state.events.reduce((s, e) => s + e.stock, 0);
    $("st-events").textContent = state.events.length;
    $("st-sold").textContent = sold.toLocaleString("es");
    $("st-stock").textContent = stock.toLocaleString("es");
    $("st-keys").textContent = health.db_size;
    $("key-chips").innerHTML = Object.entries(keys).sort((a, b) => b[1] - a[1])
      .map(([k, v]) => `<span>${esc(k)}<b>${v}</b></span>`).join("");
    $("footer-meta").textContent = `Redis ${health.redis_version} · ${health.used_memory_human} en memoria · ${health.db_size} claves`;
    const c = $("conn"); c.className = "live ok";
    c.querySelector("b").textContent = `Redis Cloud ${health.redis_version}`;
  }

  function renderTicker(activity) {
    if (!changed("ticker", activity)) return;
    const items = activity.length
      ? activity.map((a) => `<span>${esc(a.event)} · ${a.qty} ${a.qty === 1 ? "entrada vendida" : "entradas vendidas"} · ${new Date(a.at).toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" })}</span>`)
      : ["<span>Sé el primero en comprar</span>"];
    // Contenido duplicado para que el desplazamiento sea continuo (anima de 0 a -50 %).
    const row = items.join("");
    $("ticker").innerHTML = row + row;
  }

  // ----------------------------------------------------------- modal evento
  function openEvent(id) {
    state.current = id; state.qty = 1;
    renderModal(); open("event-modal");
    api(`/events/${id}`).catch(() => {}); // registra la visita (HyperLogLog)
  }

  function renderModal() {
    const e = state.events.find((x) => x.id === state.current);
    if (!e) { close("event-modal"); state.current = null; return; }
    const d = dateParts(e.date);
    const pct = e.capacity ? e.stock / e.capacity : 0;
    $("em-poster").innerHTML = posterHTML(e) + `<div class="date-badge"><b>${d.day}</b><small>${d.mon}</small></div>`;
    $("em-cat").textContent = e.category;
    $("em-name").textContent = e.name;
    $("em-date").textContent = d.long;
    $("em-venue").textContent = e.venue;
    $("em-price").textContent = `${money(e.price)} / entrada`;
    $("em-bar").style.width = `${Math.round(pct * 100)}%`;
    $("em-bar").className = pct < 0.15 ? "low" : "";
    $("em-stock").textContent = e.stock ? `Quedan ${e.stock} de ${e.capacity}` : "Agotado";
    $("em-sold").textContent = `${e.sold} vendidas · ${e.unique_visitors} visitantes únicos`;
    state.qty = Math.max(1, Math.min(state.qty, Math.min(10, e.stock || 1)));
    $("qty").textContent = state.qty;
    $("em-total").textContent = money(e.price * state.qty);
    const btn = $("em-hold");
    btn.disabled = e.stock === 0;
    btn.firstChild.textContent = !state.user ? "Inicia sesión y reserva · " : e.stock === 0 ? "Agotado · " : "Reservar · ";
    $("em-note").textContent = state.user ? "Tus entradas quedan bloqueadas 10 minutos mientras confirmas." : "Necesitas iniciar sesión para reservar.";
  }

  async function holdCurrent() {
    if (!state.user) { close("event-modal"); open("login-modal"); return; }
    const e = state.events.find((x) => x.id === state.current);
    try {
      const h = await api("/holds", { method: "POST", auth: true, body: { user_id: state.user.id, event_id: e.id, qty: state.qty } });
      toast(`${h.qty} ${h.qty === 1 ? "entrada reservada" : "entradas reservadas"}: tienes ${h.ttl_seconds / 60} min para confirmar`, "ok");
      close("event-modal");
      await refreshAll();
      toggleDrawer(true);
    } catch (err) { toast(err.message, "err"); await refreshAll(); }
  }

  // ------------------------------------------------------------ CRUD evento
  function openForm(e) {
    close("event-modal");
    $("form-title").textContent = e ? "Editar evento" : "Nuevo evento";
    $("ev-id").value = e ? e.id : "";
    $("ev-name").value = e ? e.name : "";
    $("ev-venue").value = e ? e.venue : "";
    $("ev-date").value = e ? e.date.slice(0, 16) : "";
    $("ev-category").value = e ? e.category : "";
    $("ev-price").value = e ? e.price : "";
    $("ev-capacity").value = e ? e.capacity : "";
    $("ev-capacity-label").classList.toggle("hidden", !!e);
    $("ev-capacity").required = !e;
    open("form-modal");
    $("ev-name").focus();
  }

  async function submitForm(ev) {
    ev.preventDefault();
    const id = $("ev-id").value;
    const body = { name: $("ev-name").value.trim(), venue: $("ev-venue").value.trim(), date: $("ev-date").value,
                   category: $("ev-category").value.trim().toLowerCase(), price: parseFloat($("ev-price").value) };
    try {
      if (id) { await api(`/events/${id}`, { method: "PATCH", body }); toast("Evento actualizado", "ok"); }
      else { body.capacity = parseInt($("ev-capacity").value, 10); const e = await api("/events", { method: "POST", body }); toast(`Evento «${e.name}» publicado`, "ok"); }
      close("form-modal");
      await refreshAll();
    } catch (err) { toast(err.message, "err"); }
  }

  async function deleteCurrent() {
    const e = state.events.find((x) => x.id === state.current);
    if (!confirm(`¿Eliminar «${e.name}»? Se borrarán sus claves en Redis.`)) return;
    try { await api(`/events/${e.id}`, { method: "DELETE" }); close("event-modal"); state.current = null; toast("Evento eliminado", "ok"); await refreshAll(); }
    catch (err) { toast(err.message, "err"); }
  }

  async function addStockCurrent() {
    try { const r = await api(`/events/${state.current}/stock?qty=10`, { method: "POST" }); toast(`Stock actualizado: ${r.stock} entradas`, "ok"); await refreshAll(); }
    catch (err) { toast(err.message, "err"); }
  }

  // ------------------------------------------------- reservas y órdenes
  function eventName(id) { const e = state.events.find((x) => x.id === id); return e ? e.name : `Evento #${id}`; }

  function renderHolds() {
    const n = state.holds.length;
    $("bag-count").textContent = n;
    $("bag-count").classList.toggle("hidden", !n);
    if (!state.user) return;
    if (!changed("holds", state.holds.map((h) => [h.id, h.qty, h.event_id]))) return;
    if (!n) { $("holds").innerHTML = '<p class="empty-note">No tienes reservas pendientes.</p>'; return; }
    const C = 2 * Math.PI * 28;
    $("holds").innerHTML = state.holds.map((h) => `
      <div class="hold" data-id="${esc(h.id)}">
        <div class="ring" data-ttl="${h.ttl_seconds}">
          <svg viewBox="0 0 64 64"><circle class="bg" cx="32" cy="32" r="28"/><circle class="fg" cx="32" cy="32" r="28"
            stroke-dasharray="${C}" stroke-dashoffset="${C * (1 - h.ttl_seconds / HOLD_TTL)}"/></svg>
          <span>${mmss(h.ttl_seconds)}</span>
        </div>
        <div class="hold-info">
          <b>${esc(eventName(h.event_id))}</b>
          <small>${h.qty} ${h.qty === 1 ? "entrada" : "entradas"} · reserva #${esc(h.id)}</small>
          <div class="hold-actions">
            <button class="btn btn-volt btn-sm" data-act="confirm">Confirmar compra</button>
            <button class="btn btn-line btn-sm" data-act="cancel">Soltar</button>
          </div>
        </div>
      </div>`).join("");
  }

  function renderOrders() {
    if (!state.user) return;
    if (!changed("orders", state.orders)) return;
    if (!state.orders.length) { $("orders").innerHTML = '<p class="empty-note">Aún no has comprado entradas.</p>'; return; }
    $("orders").innerHTML = state.orders.map((o) => {
      const e = state.events.find((x) => x.id === o.event_id);
      const d = e ? dateParts(e.date) : null;
      return `
      <div class="ticket ${o.status}" data-id="${esc(o.id)}">
        <div class="ticket-main">
          <b>${esc(eventName(o.event_id))}</b>
          <small>${d ? `${d.day} ${d.mon}` : ""}${e ? ` · ${esc(e.venue)}` : ""}</small>
          <small>${money(o.total)} · orden #${esc(o.id)}</small>
          <span class="status ${o.status}">${o.status === "CONFIRMED" ? "Confirmada" : "Cancelada"}</span>
          ${o.status === "CONFIRMED" ? '<button class="link-btn" data-act="cancel-order">Cancelar</button>' : ""}
        </div>
        <div class="ticket-stub"><span class="qty">×${o.qty}</span><small>entradas</small></div>
      </div>`;
    }).join("");
  }

  async function confirmHold(id) {
    try { const o = await api(`/holds/${id}/confirm`, { method: "POST", auth: true }); toast(`¡Compra confirmada! Orden #${o.id} · ${money(o.total)}`, "ok"); }
    catch (err) { toast(err.message, "err"); }
    await refreshAll();
  }
  async function cancelHold(id) {
    try { await api(`/holds/${id}`, { method: "DELETE", auth: true }); toast("Reserva liberada: las entradas vuelven a la venta", "ok"); }
    catch (err) { toast(err.message, "err"); }
    await refreshAll();
  }
  async function cancelOrder(id) {
    if (!confirm(`¿Cancelar la orden #${id}? Las entradas volverán a la venta.`)) return;
    try { await api(`/orders/${id}/cancel`, { method: "POST", auth: true }); toast("Orden cancelada", "ok"); }
    catch (err) { toast(err.message, "err"); }
    await refreshAll();
  }

  // ------------------------------------------------------------- refresco
  async function refreshMine() {
    if (!state.user) return;
    const [holds, orders] = await Promise.all([api(`/users/${state.user.id}/holds`), api(`/users/${state.user.id}/orders`)]);
    state.holds = holds; state.orders = orders;
    renderHolds(); renderOrders();
  }

  let refreshing = false;
  async function refreshAll() {
    if (refreshing) return; refreshing = true;
    try {
      const [events, ranking, activity, keys, health] = await Promise.all([
        api("/events"), api("/ranking?limit=5"), api("/activity?limit=12"), api("/stats/keys"), api("/health"),
      ]);
      state.events = events; state.ranking = ranking;
      renderChips(); renderGrid(); renderTop(); renderTicker(activity); renderStats(keys, health);
      if (state.current && !$("event-modal").classList.contains("hidden")) renderModal();
      await refreshMine();
    } catch (e) {
      const c = $("conn"); c.className = "live err"; c.querySelector("b").textContent = "Sin conexión";
      console.warn(e);
    } finally { refreshing = false; }
  }

  // ------------------------------------------------------------- eventos DOM
  $("login-open").onclick = $("hero-login").onclick = () => { loadUsers(); open("login-modal"); };
  $("avatar-btn").onclick = $("bag-btn").onclick = () => toggleDrawer(true);
  $("drawer-close").onclick = $("scrim").onclick = () => toggleDrawer(false);
  $("logout-btn").onclick = logout;
  $("admin-btn").onclick = () => openForm(null);
  $("event-form").onsubmit = submitForm;
  $("em-hold").onclick = holdCurrent;
  $("em-edit").onclick = () => openForm(state.events.find((x) => x.id === state.current));
  $("em-delete").onclick = deleteCurrent;
  $("em-stock-add").onclick = addStockCurrent;
  $("qty-minus").onclick = () => { state.qty = Math.max(1, state.qty - 1); renderModal(); };
  $("qty-plus").onclick = () => { state.qty = Math.min(10, state.qty + 1); renderModal(); };
  $("search").oninput = (e) => { state.query = e.target.value; renderGrid(); };

  $("user-list").onclick = (e) => { const b = e.target.closest(".user-pick"); if (b) login(b.dataset.id).catch((err) => toast(err.message, "err")); };
  $("user-form").onsubmit = async (e) => {
    e.preventDefault();
    try {
      const u = await api("/users", { method: "POST", body: { name: $("us-name").value.trim(), email: $("us-email").value.trim() } });
      e.target.reset(); await login(u.id);
    } catch (err) { toast(err.message, "err"); }
  };
  $("chips").onclick = (e) => { const c = e.target.closest(".chip"); if (!c) return; state.category = c.dataset.cat; renderChips(); renderGrid(); };
  $("grid").onclick = (e) => { const c = e.target.closest(".card"); if (c) openEvent(c.dataset.id); };
  $("top-row").onclick = (e) => { const c = e.target.closest(".top-card"); if (c) openEvent(c.dataset.id); };
  $("holds").onclick = (e) => {
    const b = e.target.closest("button[data-act]"); if (!b) return;
    const id = b.closest(".hold").dataset.id;
    if (b.dataset.act === "confirm") confirmHold(id); else cancelHold(id);
  };
  $("orders").onclick = (e) => {
    const b = e.target.closest("button[data-act]"); if (b) cancelOrder(b.closest(".ticket").dataset.id);
  };

  // Cuenta atrás local cada segundo; se resincroniza con Redis cada 10 s.
  setInterval(() => {
    const C = 2 * Math.PI * 28;
    let expired = false;
    document.querySelectorAll(".ring").forEach((r) => {
      const t = parseInt(r.dataset.ttl, 10) - 1; r.dataset.ttl = t;
      r.querySelector("span").textContent = t > 0 ? mmss(t) : "0:00";
      r.querySelector(".fg").style.strokeDashoffset = C * (1 - Math.max(0, t) / HOLD_TTL);
      r.classList.toggle("urgent", t < 60);
      if (t <= 0) expired = true;
    });
    if (expired) { toast("Una reserva expiró: las entradas vuelven a la venta"); delete lastRender.holds; refreshAll(); }
    if (state.user && state.sessionTtl > 0) { state.sessionTtl -= 1; $("session-ttl").textContent = mmss(state.sessionTtl); }
  }, 1000);
  setInterval(refreshAll, 10000);

  refreshAll().then(restoreSession);
})();
