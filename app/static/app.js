/* TicketFlash - interfaz web. Consume la API REST (FastAPI) que habla con Redis Cloud. */
(() => {
  const $ = (id) => document.getElementById(id);
  const state = { token: null, user: null, sessionTtl: 0, holds: [], category: "" };

  // ---------------------------------------------------------------- API ---
  async function api(path, { method = "GET", body, auth = false } = {}) {
    const headers = { "Content-Type": "application/json" };
    if (auth && state.token) headers["X-Session-Token"] = state.token;
    const res = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
    if (res.status === 204) return null;
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      if (res.status === 401 && auth) { endSession("La sesión expiró en Redis (TTL agotado)."); }
      throw new Error(data.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : res.statusText);
    }
    return data;
  }

  function toast(msg, kind = "") {
    const el = $("toast");
    el.textContent = msg; el.className = `toast ${kind}`;
    clearTimeout(el._t); el._t = setTimeout(() => el.classList.add("hidden"), 3500);
  }
  const money = (n) => Number(n).toFixed(2) + " €";
  const fmtDate = (s) => { const d = new Date(s); return isNaN(d) ? s : d.toLocaleString("es", { dateStyle: "medium", timeStyle: "short" }); };
  const mmss = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  // Evita re-dibujar un bloque si sus datos no cambiaron (mantiene foco y botones estables).
  const lastRender = {};
  const changed = (key, data) => { const j = JSON.stringify(data); if (lastRender[key] === j) return false; lastRender[key] = j; return true; };

  // ------------------------------------------------------------ sesión ---
  async function loadUsers() {
    const users = await api("/users");
    const sel = $("user-select");
    sel.innerHTML = '<option value="">Selecciona usuario…</option>' +
      users.map((u) => `<option value="${u.id}">${u.name} (${u.email})</option>`).join("");
  }

  async function login() {
    const userId = $("user-select").value;
    if (!userId) return toast("Elige un usuario", "err");
    const s = await api("/auth/login", { method: "POST", body: { user_id: userId } });
    state.token = s.token; state.sessionTtl = s.ttl_seconds;
    const me = await api("/auth/me", { auth: true });
    state.user = me.user;
    $("session-user").textContent = `👤 ${me.user.name}`;
    $("user-select").classList.add("hidden"); $("login-btn").classList.add("hidden"); $("new-user-btn").classList.add("hidden");
    $("session-info").classList.remove("hidden");
    toast(`Sesión creada en Redis con TTL de ${s.ttl_seconds} s`, "ok");
    await refreshAll();
  }

  function endSession(msg) {
    state.token = null; state.user = null; state.holds = []; delete lastRender.holds; delete lastRender.orders;
    $("user-select").classList.remove("hidden"); $("login-btn").classList.remove("hidden"); $("new-user-btn").classList.remove("hidden");
    $("session-info").classList.add("hidden");
    $("holds-list").innerHTML = '<p class="muted">Inicia sesión para ver tus reservas.</p>';
    $("orders-list").innerHTML = '<p class="muted">—</p>';
    if (msg) toast(msg);
    renderEvents();
  }

  async function logout() {
    try { await api("/auth/logout", { method: "POST", auth: true }); } catch (_) { /* ya expiró */ }
    endSession("Sesión cerrada (clave eliminada de Redis).");
  }

  async function refreshSessionTtl() {
    if (!state.token) return;
    try {
      const me = await api("/auth/me", { auth: true });
      state.sessionTtl = me.session_ttl;
      $("session-ttl").textContent = mmss(me.session_ttl);
    } catch (_) { /* manejado en api() */ }
  }

  // ----------------------------------------------------------- eventos ---
  let events = [];
  async function loadEvents() {
    const q = state.category ? `?category=${encodeURIComponent(state.category)}` : "";
    events = await api("/events" + q);
    renderEvents();
    const cats = await api("/events/categories");
    const sel = $("category-filter");
    const current = sel.value;
    sel.innerHTML = '<option value="">Todas las categorías</option>' + cats.map((c) => `<option value="${c}">${c}</option>`).join("");
    sel.value = current;
  }

  function renderEvents() {
    const list = $("events-list");
    if (!changed("events", [events, !!state.token])) return;
    if (!events.length) { list.innerHTML = '<p class="muted">No hay eventos. Crea uno o ejecuta scripts/seed.py.</p>'; return; }
    list.innerHTML = events.map((e) => {
      const pct = e.capacity ? Math.round((e.stock / e.capacity) * 100) : 0;
      const cls = e.stock === 0 ? "out" : pct < 25 ? "low" : "";
      const canBuy = !!state.token && e.stock > 0;
      return `
      <div class="card event" data-id="${e.id}">
        <div class="row between"><span class="cat">${e.category}</span><span class="muted">#${e.id}</span></div>
        <div class="title">${e.name}</div>
        <div class="meta">📍 ${e.venue} · 📅 ${fmtDate(e.date)}</div>
        <div class="row between"><span class="price">${money(e.price)}</span><span class="muted">${e.sold} vendidas · ${e.unique_visitors} visitas únicas</span></div>
        <div class="bar ${cls}"><i style="width:${pct}%"></i></div>
        <div class="stock-line"><span>Stock: <b>${e.stock}</b> / ${e.capacity}</span><span>${e.stock === 0 ? "AGOTADO" : pct + "% disponible"}</span></div>
        <div class="row actions">
          <input class="qty" type="number" min="1" max="10" value="1" ${canBuy ? "" : "disabled"}>
          <button class="btn primary sm act-hold" ${canBuy ? "" : "disabled"} title="${state.token ? "" : "Inicia sesión para reservar"}">Reservar</button>
          <span class="muted" style="flex:1"></span>
          <button class="btn ghost sm act-edit" title="Editar (PATCH)">✏️</button>
          <button class="btn ghost sm act-stock" title="Añadir 10 al stock (INCRBY)">+10</button>
          <button class="btn danger sm act-del" title="Eliminar (DEL)">🗑</button>
        </div>
      </div>`;
    }).join("");
  }

  async function createHold(eventId, qty) {
    try {
      const h = await api("/holds", { method: "POST", auth: true, body: { user_id: state.user.id, event_id: eventId, qty } });
      toast(`Reserva #${h.id} creada: ${qty} entradas bloqueadas ${h.ttl_seconds} s`, "ok");
      await refreshAll();
    } catch (e) { toast(e.message, "err"); }
  }

  async function deleteEvent(id) {
    if (!confirm(`¿Eliminar el evento #${id} y todas sus claves en Redis?`)) return;
    try { await api(`/events/${id}`, { method: "DELETE" }); toast("Evento eliminado", "ok"); await refreshAll(); }
    catch (e) { toast(e.message, "err"); }
  }

  async function addStock(id) {
    try { const r = await api(`/events/${id}/stock?qty=10`, { method: "POST" }); toast(`Stock ahora: ${r.stock}`, "ok"); await loadEvents(); }
    catch (e) { toast(e.message, "err"); }
  }

  function openEventForm(ev) {
    $("user-form").classList.add("hidden");
    const f = $("event-form"); f.classList.remove("hidden");
    $("event-form-title").textContent = ev ? `Editar evento #${ev.id}` : "Nuevo evento";
    $("ev-id").value = ev ? ev.id : "";
    $("ev-name").value = ev ? ev.name : ""; $("ev-venue").value = ev ? ev.venue : "";
    $("ev-date").value = ev ? ev.date.slice(0, 16) : ""; $("ev-category").value = ev ? ev.category : "";
    $("ev-price").value = ev ? ev.price : ""; $("ev-capacity").value = ev ? ev.capacity : "";
    $("ev-capacity").disabled = !!ev; $("ev-capacity-label").style.opacity = ev ? .5 : 1;
    f.scrollIntoView({ behavior: "smooth" });
  }

  async function submitEventForm(e) {
    e.preventDefault();
    const id = $("ev-id").value;
    const body = { name: $("ev-name").value.trim(), venue: $("ev-venue").value.trim(), date: $("ev-date").value,
                   category: $("ev-category").value.trim().toLowerCase(), price: parseFloat($("ev-price").value) };
    try {
      if (id) { await api(`/events/${id}`, { method: "PATCH", body }); toast("Evento actualizado (HSET)", "ok"); }
      else { body.capacity = parseInt($("ev-capacity").value, 10); await api("/events", { method: "POST", body }); toast("Evento creado", "ok"); }
      $("event-form").classList.add("hidden"); await loadEvents();
    } catch (err) { toast(err.message, "err"); }
  }

  async function submitUserForm(e) {
    e.preventDefault();
    try {
      const u = await api("/users", { method: "POST", body: { name: $("us-name").value.trim(), email: $("us-email").value.trim() } });
      $("user-form").classList.add("hidden"); await loadUsers(); $("user-select").value = u.id;
      toast(`Usuario #${u.id} creado`, "ok");
    } catch (err) { toast(err.message, "err"); }
  }

  // ----------------------------------------------------- reservas/órdenes ---
  async function loadHolds() {
    if (!state.user) return;
    state.holds = await api(`/users/${state.user.id}/holds`);
    renderHolds();
  }

  function renderHolds() {
    const el = $("holds-list");
    if (!state.user) return;
    if (!changed("holds", state.holds.map((h) => [h.id, h.qty, h.event_id]))) return;
    if (!state.holds.length) { el.innerHTML = '<p class="muted">Sin reservas activas.</p>'; return; }
    el.innerHTML = state.holds.map((h) => {
      const ev = events.find((e) => e.id === h.event_id);
      const urgent = h.ttl_seconds < 60;
      return `<div class="item" data-id="${h.id}">
        <div class="row between"><span class="t">${ev ? ev.name : "Evento " + h.event_id}</span><span class="countdown ${urgent ? "urgent" : ""}" data-ttl="${h.ttl_seconds}">${mmss(h.ttl_seconds)}</span></div>
        <div class="muted">${h.qty} entradas · reserva #${h.id}</div>
        <div class="row actions">
          <button class="btn ok sm act-confirm">Confirmar compra</button>
          <button class="btn ghost sm act-cancel">Cancelar</button>
        </div></div>`;
    }).join("");
  }

  async function confirmHold(id) {
    try {
      const o = await api(`/holds/${id}/confirm`, { method: "POST", auth: true });
      toast(`Orden #${o.id} confirmada: ${money(o.total)}`, "ok"); await refreshAll();
    } catch (e) { toast(e.message, "err"); await refreshAll(); }
  }

  async function cancelHold(id) {
    try { await api(`/holds/${id}`, { method: "DELETE", auth: true }); toast("Reserva cancelada, stock devuelto", "ok"); await refreshAll(); }
    catch (e) { toast(e.message, "err"); }
  }

  async function loadOrders() {
    if (!state.user) return;
    const orders = await api(`/users/${state.user.id}/orders`);
    const el = $("orders-list");
    if (!changed("orders", orders)) return;
    if (!orders.length) { el.innerHTML = '<p class="muted">Sin órdenes todavía.</p>'; return; }
    el.innerHTML = orders.map((o) => {
      const ev = events.find((e) => e.id === o.event_id);
      return `<div class="item" data-id="${o.id}">
        <div class="row between"><span class="t">${ev ? ev.name : "Evento " + o.event_id}</span><span class="status ${o.status}">${o.status}</span></div>
        <div class="muted">${o.qty} × ${money(o.unit_price)} = <b>${money(o.total)}</b> · orden #${o.id}</div>
        ${o.status === "CONFIRMED" ? '<div class="row actions"><button class="btn ghost sm act-cancel-order">Cancelar orden</button></div>' : ""}
      </div>`;
    }).join("");
  }

  async function cancelOrder(id) {
    try { await api(`/orders/${id}/cancel`, { method: "POST", auth: true }); toast("Orden cancelada, stock y ranking actualizados", "ok"); await refreshAll(); }
    catch (e) { toast(e.message, "err"); }
  }

  // ---------------------------------------------------------- analítica ---
  async function loadAnalytics() {
    const [ranking, activity, keys, health] = await Promise.all([
      api("/ranking?limit=5"), api("/activity?limit=8"), api("/stats/keys"), api("/health"),
    ]);
    $("ranking-list").innerHTML = ranking.map((r) => `<li>${r.name} — <b>${r.sold}</b> entradas</li>`).join("") || '<li class="muted">Sin ventas</li>';
    $("activity-list").innerHTML = activity.map((a) => `<li>${new Date(a.at).toLocaleTimeString("es")} · usuario #${a.user_id} compró ${a.qty} × ${a.event}</li>`).join("") || '<li>—</li>';
    $("keys-stats").innerHTML = Object.entries(keys).map(([k, v]) => `<span class="chip">${k} <b>${v}</b></span>`).join("");
    const c = $("conn");
    c.textContent = `● Redis Cloud ${health.redis_version} · ${health.db_size} claves · ${health.used_memory_human}`;
    c.className = "conn ok";
  }

  async function refreshAll() {
    try { await loadEvents(); await Promise.all([loadHolds(), loadOrders(), loadAnalytics(), refreshSessionTtl()]); renderHolds(); }
    catch (e) { $("conn").textContent = "Sin conexión: " + e.message; $("conn").className = "conn err"; }
  }

  // ---------------------------------------------------------- eventos UI ---
  $("login-btn").onclick = login;
  $("logout-btn").onclick = logout;
  $("new-user-btn").onclick = () => { $("event-form").classList.add("hidden"); $("user-form").classList.toggle("hidden"); };
  $("us-cancel").onclick = () => $("user-form").classList.add("hidden");
  $("user-form").onsubmit = submitUserForm;
  $("toggle-admin").onclick = () => openEventForm(null);
  $("ev-cancel").onclick = () => $("event-form").classList.add("hidden");
  $("event-form").onsubmit = submitEventForm;
  $("category-filter").onchange = (e) => { state.category = e.target.value; loadEvents(); };

  $("events-list").addEventListener("click", (e) => {
    const card = e.target.closest(".event"); if (!card) return;
    const id = card.dataset.id;
    if (e.target.classList.contains("act-hold")) createHold(id, parseInt(card.querySelector(".qty").value, 10) || 1);
    if (e.target.classList.contains("act-del")) deleteEvent(id);
    if (e.target.classList.contains("act-stock")) addStock(id);
    if (e.target.classList.contains("act-edit")) openEventForm(events.find((x) => x.id === id));
  });
  $("holds-list").addEventListener("click", (e) => {
    const item = e.target.closest(".item"); if (!item) return;
    if (e.target.classList.contains("act-confirm")) confirmHold(item.dataset.id);
    if (e.target.classList.contains("act-cancel")) cancelHold(item.dataset.id);
  });
  $("orders-list").addEventListener("click", (e) => {
    const item = e.target.closest(".item"); if (!item) return;
    if (e.target.classList.contains("act-cancel-order")) cancelOrder(item.dataset.id);
  });

  // Cuenta atrás local (1 s) + sincronización con Redis (cada 10 s).
  setInterval(() => {
    document.querySelectorAll(".countdown").forEach((el) => {
      let t = parseInt(el.dataset.ttl, 10) - 1; el.dataset.ttl = t;
      if (t <= 0) { el.textContent = "expirada"; loadHolds().then(loadEvents); } else { el.textContent = mmss(t); if (t < 60) el.classList.add("urgent"); }
    });
    if (state.token && state.sessionTtl > 0) { state.sessionTtl -= 1; $("session-ttl").textContent = mmss(state.sessionTtl); }
  }, 1000);
  setInterval(refreshAll, 10000);

  loadUsers().then(refreshAll);
})();
