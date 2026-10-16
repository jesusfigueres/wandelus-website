/*
 * monbi-site.js — lo que toda web de negocio de la demo de MONBI necesita, sea cual sea su diseño:
 * la visita (visitor_key / session_key), el consentimiento, el tracking (dataLayer + su Serpis, por su
 * backend), el chat con el Salesman (en streaming), el alta y el foro. Expone `window.MONBI`.
 *
 * Todas las llamadas son RELATIVAS (`api/...`): la web se sirve igual en su dominio que bajo
 * `demos.monbi.ai/websites/<negocio>/`.
 */
(function () {
  "use strict";
  const LS = (() => { try { return window.localStorage; } catch (e) { return null; } })();
  const SS = (() => { try { return window.sessionStorage; } catch (e) { return null; } })();
  const get = (s, k) => { try { return s && s.getItem(k); } catch (e) { return null; } };
  const set = (s, k, v) => { try { s && s.setItem(k, v); } catch (e) { /* sin almacenamiento */ } };
  const uid = (p) => p + Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);

  let visitor = get(LS, "monbi_visitor") || uid("v_"); set(LS, "monbi_visitor", visitor);
  let session = get(SS, "monbi_session"); const nueva = !session;
  if (!session) { session = uid("s_"); set(SS, "monbi_session", session); }
  let customer = get(LS, "monbi_customer") || null;
  let consent = (() => { try { return JSON.parse(get(LS, "monbi_consent") || "null"); } catch (e) { return null; } })();
  let currentPage = null;
  let conversation = get(SS, "monbi_conversation") || uid("conv_"); set(SS, "monbi_conversation", conversation);

  window.dataLayer = window.dataLayer || [];
  const post = (path, body) => fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body), keepalive: true }).then((r) => r.ok ? r.json() : Promise.reject(r.status));

  function loadGTM(id) {
    if (!id || document.getElementById("gtm-script")) return;
    window.dataLayer.push({ "gtm.start": Date.now(), event: "gtm.js" });
    const s = document.createElement("script"); s.id = "gtm-script"; s.async = true;
    s.src = "https://www.googletagmanager.com/gtm.js?id=" + encodeURIComponent(id);
    document.head.appendChild(s);
  }

  let config = { gtm_id: null };
  fetch("api/config").then((r) => r.json()).then((c) => { config = c; if (consent && consent.analytics) loadGTM(c.gtm_id); }).catch(() => {});

  function utm() {
    const q = new URLSearchParams(location.search); const o = {};
    ["utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"].forEach((k) => { if (q.get(k)) o[k] = q.get(k); });
    return o;
  }

  function openSession(pageType) {
    let ref = null; try { ref = document.referrer ? new URL(document.referrer).host : null; } catch (e) { /* */ }
    post("api/session", Object.assign({ session_key: session, visitor_key: visitor, landing_page_type: pageType,
      referrer_host: ref, device: /Mobi/i.test(navigator.userAgent) ? "mobile" : "desktop",
      consent_analytics: consent ? !!consent.analytics : null, consent_marketing: consent ? !!consent.marketing : null }, utm()))
      .catch(() => {});
  }

  /** Un evento: al dataLayer (para GTM) y a su Serpis, por el backend. */
  function track(event, opts) {
    opts = opts || {};
    const ev = { event, page: opts.page || currentPage, element: opts.element || null, value: opts.value || null, visitor_key: visitor, at: new Date().toISOString() };
    window.dataLayer.push(Object.assign({ event: "monbi_" + event, monbi_page: ev.page, monbi_element: ev.element }, opts.value || {}));
    return post("api/track", { session_key: session, customer_key: customer, consent, events: [ev] }).catch(() => {});
  }

  /** Cambio de vista: abre la visita la primera vez y registra `<página>_view`. */
  function page(pageType) {
    if (pageType === currentPage) return;
    const primera = currentPage === null;
    currentPage = pageType;
    if (primera && nueva) openSession(pageType);
    track("page_view", { page: pageType });
  }

  /** Un turno con el Salesman. `onDelta(textoAcumulado)` mientras escribe; resuelve con el texto final. */
  async function chat(message, onDelta) {
    track("chat_message", { element: "chat" });
    const r = await fetch("api/chat", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_key: session, conversation_id: conversation, message }) });
    if (!r.ok || !r.body) throw new Error("chat " + r.status);
    const reader = r.body.getReader(); const dec = new TextDecoder();
    let buf = "", text = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
        if (!line) continue;
        let o; try { o = JSON.parse(line); } catch (e) { continue; }
        if (o.error) throw new Error(o.error);
        if (typeof o.delta === "string") { text += o.delta; onDelta && onDelta(text); }
        if (o.done && typeof o.reply === "string" && !text) { text = o.reply; onDelta && onDelta(text); }
      }
    }
    return text;
  }

  async function register(data) {
    const r = await post("api/register", Object.assign({ session_key: session, consent }, data));
    customer = r.customer_key; set(LS, "monbi_customer", customer);
    return r;
  }

  let catalogPromise = null;
  const catalog = () => (catalogPromise = catalogPromise || fetch("api/catalog").then((r) => r.json()));
  const forum = () => fetch("api/forum").then((r) => r.json());
  const newThread = (t) => post("api/forum", Object.assign({ session_key: session, customer_key: customer }, t));
  const comment = (c) => post("api/comment", Object.assign({ session_key: session, customer_key: customer }, c));

  // ── Consentimiento: lo mínimo legal; sin aceptar, no se carga GTM ──────────────────────────
  function banner() {
    if (consent) return;
    const b = document.createElement("div");
    b.setAttribute("role", "dialog");
    b.style.cssText = "position:fixed;left:16px;right:16px;bottom:16px;z-index:9999;max-width:680px;margin:0 auto;background:#17181C;color:#F4F0E8;border-radius:14px;padding:16px 18px;font:14px/1.45 system-ui,sans-serif;box-shadow:0 10px 30px rgba(0,0,0,.25);display:flex;gap:14px;align-items:center;flex-wrap:wrap";
    b.innerHTML = '<div style="flex:1;min-width:220px">Usamos cookies para medir el uso de la web y mejorarla. Es una web de demostración con datos ficticios.</div>' +
      '<button data-c="no" style="background:transparent;color:inherit;border:1px solid #F4F0E8;border-radius:999px;padding:8px 14px;cursor:pointer">Rechazar</button>' +
      '<button data-c="si" style="background:#F4F0E8;color:#17181C;border:none;border-radius:999px;padding:8px 14px;cursor:pointer;font-weight:600">Aceptar</button>';
    b.addEventListener("click", (e) => {
      const v = e.target && e.target.getAttribute("data-c"); if (!v) return;
      consent = { analytics: v === "si", marketing: v === "si" }; set(LS, "monbi_consent", JSON.stringify(consent));
      track("consent", { element: v === "si" ? "accept" : "reject" });
      if (consent.analytics) loadGTM(config.gtm_id);
      b.remove();
    });
    document.body.appendChild(b);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", banner); else banner();

  window.MONBI = { page, track, chat, register, catalog, forum, newThread, comment,
    get session() { return session; }, get customer() { return customer; }, get visitor() { return visitor; } };
})();
