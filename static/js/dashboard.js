/* Shared dashboard behaviour: theme, sidebar, toasts, confirm dialogs, helpers. */
(function () {
  const root = document.documentElement;
  function apply(t) { root.setAttribute('data-bs-theme', t); document.querySelectorAll('[data-theme-ico]').forEach(e => e.textContent = t === 'dark' ? 'light_mode' : 'dark_mode'); }
  document.addEventListener('DOMContentLoaded', () => {
    const $$ = (s, r = document) => [...r.querySelectorAll(s)];
    apply(root.getAttribute('data-bs-theme') || 'light');
    $$('[data-theme-btn]').forEach(b => b.addEventListener('click', () => {
      const t = root.getAttribute('data-bs-theme') === 'dark' ? 'light' : 'dark'; apply(t);
      try { localStorage.setItem('jelly-theme', t); } catch (e) {}
    }));
    const body = document.body;
    $$('[data-sb-toggle]').forEach(b => b.addEventListener('click', () => { if (innerWidth >= 992) body.classList.toggle('sb-mini'); else body.classList.toggle('sb-open'); }));
    const ov = document.querySelector('.sb-ov'); if (ov) ov.addEventListener('click', () => body.classList.remove('sb-open'));

    $$('.toast[data-autohide]').forEach(t => setTimeout(() => bootstrap.Toast.getOrCreateInstance(t).hide(), +t.dataset.autohide));

    /* any <form data-confirm="Question?"> asks first, then submits normally (POST + CSRF) */
    const cm = document.getElementById('confirmModal'), cmod = cm && new bootstrap.Modal(cm); let pending = null;
    document.addEventListener('submit', e => {
      const f = e.target.closest('form[data-confirm]'); if (!f || f.dataset.confirmed || !cm) return;      // no dialog on this page: submit normally
      e.preventDefault(); pending = f; cm.querySelector('.msg').textContent = f.dataset.confirm; cmod.show();
    });
    if (cm) cm.querySelector('.ok').addEventListener('click', () => { if (pending) { pending.dataset.confirmed = '1'; cmod.hide(); pending.requestSubmit ? pending.requestSubmit() : pending.submit(); } });

    /* copy-to-clipboard buttons */
    document.addEventListener('click', e => {
      const b = e.target.closest('[data-copy]'); if (!b) return;
      (navigator.clipboard ? navigator.clipboard.writeText(b.dataset.copy) : Promise.reject()).then(() => { b.title = 'Copied'; b.classList.add('btn-success'); setTimeout(() => b.classList.remove('btn-success'), 1200); }).catch(() => {});
    });

    /* image preview for <input type=file data-preview="#imgId"> */
    $$('input[type=file][data-preview]').forEach(inp => inp.addEventListener('change', () => {
      const img = document.querySelector(inp.dataset.preview), f = inp.files[0];
      if (img && f) { img.src = URL.createObjectURL(f); img.classList.remove('d-none'); }
    }));

    /* recording form: link vs file */
    $$('[name=rec_type]').forEach(r => r.addEventListener('change', () => {
      const file = document.querySelector('[name=rec_type]:checked').value === 'file';
      document.getElementById('recLinkBox').classList.toggle('d-none', file); document.getElementById('recFileBox').classList.toggle('d-none', !file);
    }));

    /* live countdowns: <span data-countdown="2026-10-14T19:00:00+05:00"> */
    function tick() {
      $$('[data-countdown]').forEach(el => {
        const ms = new Date(el.dataset.countdown) - Date.now(); if (isNaN(ms)) return;
        if (ms <= 0) { el.textContent = 'now'; return; }
        const m = Math.floor(ms / 60000), d = Math.floor(m / 1440), h = Math.floor(m % 1440 / 60), mm = m % 60;
        el.textContent = d ? `${d}d ${h}h ${mm}m` : h ? `${h}h ${mm}m` : `${mm} min`;
      });
    }
    tick(); setInterval(tick, 30000);



    /* remember the open tab of pill-style tab screens (Site content, Payments & promos) across saves and reloads */
    const pills = $$('[data-bs-toggle="pill"][data-bs-target]');
    if (pills.length) {
      const key = 'tab:' + location.pathname;
      const wanted = location.hash || sessionStorage.getItem(key);
      const btn = wanted && pills.find(b => b.dataset.bsTarget === wanted);
      if (btn && window.bootstrap) window.bootstrap.Tab.getOrCreateInstance(btn).show();
      pills.forEach(b => b.addEventListener('shown.bs.tab', () => sessionStorage.setItem(key, b.dataset.bsTarget)));
    }

    /* live numbers: <span data-live="pending"> refreshes from the role's api/summary/ endpoint (body[data-live-url]) every 30s */
    const liveEls = $$('[data-live]');
    if ((liveEls.length || $$('[data-live-badge]').length) && document.body.dataset.liveUrl) {
      const dot = document.getElementById('liveDot');
      const refresh = () => fetch(document.body.dataset.liveUrl, {headers: {'X-Requested-With': 'fetch'}, credentials: 'same-origin'})
        .then(r => r.ok ? r.json() : Promise.reject()).then(d => {
          $$('[data-live]').forEach(el => { const v = d[el.dataset.live]; if (v !== undefined && String(el.textContent).trim() !== String(v)) el.textContent = v; });
          $$('[data-live-badge]').forEach(el => { const v = ({'adminpanel:payments': d.pending, 'adminpanel:contact': d.messages, 'teacher:live': d.recordings, 'student:billing': d.due, 'student:live': d.live_now})[el.dataset.liveBadge] || 0; el.textContent = v; el.style.display = v ? '' : 'none'; });
          if (dot) { dot.textContent = '● live'; dot.className = 'chip green'; }
        }).catch(() => { if (dot) { dot.textContent = '● offline'; dot.className = 'chip red'; } });
      setInterval(() => { if (!document.hidden) refresh(); }, 30000);
      document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
    }

    /* auto-submit filters, tab from URL hash */
    $$('[data-autosubmit]').forEach(s => s.addEventListener('change', () => s.form.submit()));
    if (location.hash) { try { const t = document.querySelector(`[data-bs-target="${CSS.escape(location.hash)}"]`); if (t) bootstrap.Tab.getOrCreateInstance(t).show(); } catch (e) {} }      // a hash like #"] must not stop the script

    /* watch modal: load video only when opened */
    const wm = document.getElementById('watchM');
    if (wm) wm.addEventListener('hidden.bs.modal', () => { const b = document.getElementById('watchBox'); if (b) b.innerHTML = ''; });
  });
})();
