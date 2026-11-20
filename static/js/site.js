/* Public site behaviour: navbar, toasts, password helpers, enroll summary. */
document.addEventListener('DOMContentLoaded', () => {
  const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];

  /* error pages: the "Go back" button (a javascript: link would be blocked by the Content-Security-Policy) */
  document.addEventListener('click', e => { if (e.target.closest('[data-back]')) { e.preventDefault(); history.length > 1 ? history.back() : location.assign('/'); } });

  /* navbar: hide on scroll down, mobile menu */
  const header = $('.site-header'), nav = $('.site-header .nav'), panel = $('.mobile-panel'), tog = $('.nav-toggle');
  if (header && nav) {
    let last = scrollY;
    addEventListener('scroll', () => {
      const y = scrollY, open = panel && panel.classList.contains('open');
      if (!open) header.classList.toggle('header-hidden', y > last && y > 120);
      nav.classList.toggle('is-scrolled', y > 12); last = y;
    }, { passive: true });
  }
  if (tog && panel) tog.addEventListener('click', () => { const o = panel.classList.toggle('open'); tog.setAttribute('aria-expanded', o); });

  /* Django messages -> auto-hide toasts */
  $$('.toast[data-autohide]').forEach(t => setTimeout(() => bootstrap.Toast.getOrCreateInstance(t).hide(), +t.dataset.autohide));

  /* search modal focus, YouTube preview lazy load */
  const sm = $('#searchM'); if (sm) sm.addEventListener('shown.bs.modal', () => $('#searchIn').focus());
  const pv = $('#prev'); if (pv) {
    const f = $('iframe', pv);
    pv.addEventListener('shown.bs.modal', () => { f.src = f.dataset.src; });
    pv.addEventListener('hidden.bs.modal', () => { f.src = ''; });
  }

  /* password show/hide + strength meter */
  $$('[data-pw]').forEach(b => b.addEventListener('click', () => {
    const i = document.getElementById(b.dataset.pw); i.type = i.type === 'password' ? 'text' : 'password'; b.textContent = i.type === 'password' ? '👁' : '🙈';
  }));
  const rp = $('#id_password1'), bar = $('.strength i'), sl = $('#strLabel');
  if (rp && bar) rp.addEventListener('input', () => {
    const v = rp.value; let s = 0;
    if (v.length >= 8) s++; if (/[A-Z]/.test(v) && /[a-z]/.test(v)) s++; if (/\d/.test(v)) s++; if (/[^A-Za-z0-9]/.test(v)) s++;
    const c = ['#D6453B', '#E0A916', '#4C9BE8', '#17A360'], t = ['Too weak', 'Fair', 'Good', 'Strong'];
    bar.style.width = (v ? (s || 1) * 25 : 0) + '%'; bar.style.background = c[Math.max(0, s - 1)];
    sl.textContent = v ? t[Math.max(0, s - 1)] : 'Use 8+ characters with a number and a symbol';
  });

  /* enroll page: live summary, batch switching, promo code (checked on the server) */
  const ef = $('#enrollForm');
  if (ef) {
    const sel = $('#enCourse'), fmt = n => 'Rs ' + Math.round(n).toLocaleString('en-US'); let promo = 0;
    function sum() {
      const o = sel && sel.selectedOptions[0];
      if (!o) return;                                   // no published course yet: nothing to summarise
      const pr = +o.dataset.price, og = +o.dataset.orig;
      $$('.batch-group').forEach(g => g.classList.toggle('d-none', g.dataset.course !== sel.value));
      const group = $(`.batch-group[data-course="${sel.value}"]`);
      if (group && !$('input[name=batch]:checked', group)) { const first = $('input[name=batch]:not(:disabled)', group); if (first) first.checked = true; }
      $$('input[name=batch]').forEach(r => { if (r.closest('.batch-group').classList.contains('d-none')) r.checked = false; });
      const bt = group && $('input[name=batch]:checked', group), pm = $('input[name=gateway]:checked', ef);
      $('#sImg').src = o.dataset.img; $('#sTitle').textContent = o.dataset.title; $('#sWeeks').textContent = o.dataset.weeks;
      $('#sOrig').textContent = fmt(og); $('#sDisc').textContent = '- ' + fmt(og - pr);
      $('#sPromoRow').style.display = promo ? '' : 'none'; $('#sPromo').textContent = '- ' + fmt(pr * promo / 100);
      $('#sTotal').textContent = fmt(pr * (100 - promo) / 100); $('#sBatch').textContent = bt ? bt.dataset.label : 'Next batch';
      if (pm) $$('.pay-info').forEach(x => x.classList.toggle('d-none', x.dataset.for !== pm.value));
    }
    ef.addEventListener('change', sum);
    $('#applyPromo').addEventListener('click', async () => {
      const code = $('#promo').value.trim(), m = $('#promoMsg');
      let j;
      try { const r = await fetch(`${ef.dataset.promoUrl}?code=${encodeURIComponent(code)}`); j = await r.json(); if (!r.ok) j = {valid: false, message: j.error || 'Please try again in a moment.'}; }
      catch (e) { j = {valid: false, message: 'Could not check the code. Please try again.'}; }
      promo = j.valid ? j.percent : 0; m.textContent = j.message; m.className = 'small ' + (j.valid ? 'text-success' : 'text-danger'); sum();
    });
    if ($('#promo').value.trim()) $('#applyPromo').click();
    sum();
  }
});
