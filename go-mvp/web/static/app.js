(() => {
  if (window.workshopInitialized) return;
  window.workshopInitialized = true;
  let lastActivity = Date.now(), lastTouch = 0, pending = 0;
 const clocks = new WeakMap();
  const activity = () => {
    lastActivity = Date.now();
    if (document.body.dataset.role !== 'employee' || lastActivity - lastTouch < 2000) return;
    lastTouch = lastActivity;
    fetch('/session/touch', {method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: new URLSearchParams({csrf: document.body.dataset.csrf})}).catch(() => {});
  };
  ['pointerdown', 'keydown', 'input'].forEach(event => document.addEventListener(event, activity));
  setInterval(() => {
    for (const label of document.querySelectorAll('[data-elapsed-us]')) { if (!clocks.has(label)) clocks.set(label, Date.now()); const elapsed = Number(label.dataset.elapsedUs) + (Date.now() - clocks.get(label)) * 1000 * Number(label.dataset.activeMembers); label.textContent = (elapsed / 60000000).toFixed(2); }
    if (document.body.dataset.role !== 'employee') return;
    if (pending) activity();
    const remaining = Math.max(0, 15 - Math.floor((Date.now() - lastActivity) / 1000));
    const label = document.getElementById('idle-count'); if (label) label.textContent = remaining;
    if (remaining === 0) {
      document.body.dataset.role = 'guest';
      // Clear the visible identity even when the network is unavailable; the server independently enforces idle expiry.
      const main = document.querySelector('main'); if (main) main.replaceChildren(Object.assign(document.createElement('p'), {textContent: '身份已退出，后台生产计时继续。'}));
      fetch('/logout', {method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: new URLSearchParams({csrf: document.body.dataset.csrf})}).finally(() => location.replace('/login'));
    }
  }, 1000);
  document.addEventListener('submit', async event => {
    const form = event.target;
    if (form.method !== 'post' || !new URL(form.action).pathname.startsWith('/action/')) return;
    event.preventDefault();
    if (form.dataset.submitting) return;
    const action = event.submitter?.hasAttribute('formaction') ? event.submitter.formAction : form.action;
    const body = new URLSearchParams(new FormData(form));
    const buttons = [...form.querySelectorAll('button')];
    const disabled = buttons.map(button => button.disabled);
    form.dataset.submitting = 'true'; form.setAttribute('aria-busy', 'true');
    buttons.forEach(button => button.disabled = true);
    let feedback = form.querySelector('.form-feedback');
    if (!feedback) { feedback = document.createElement('div'); form.append(feedback); }
    feedback.className = 'form-feedback alert'; feedback.setAttribute('role', 'status');
    feedback.textContent = '正在保存，请稍候…';
    pending++; activity();
    try {
      const response = await fetch(action, {method: 'POST', headers: {'Accept':'application/json'}, body, signal:AbortSignal.timeout(35000)});
      if (response.redirected) { location.replace(response.url); return; }
      const result = await response.json();
      if (!response.ok || result.error) throw new Error(result.error || '保存失败，请重试');
      const next = new URL(result.redirect, location.href);
      if (next.pathname + next.search === location.pathname + location.search) location.reload();
      else location.assign(next.href);
    } catch (error) {
      feedback.className = 'form-feedback alert alert-error'; feedback.setAttribute('role', 'alert');
      feedback.textContent = error instanceof SyntaxError || error.name === 'TimeoutError' || error instanceof TypeError
        ? '暂未收到保存回执，输入已保留。请重试同一请求，系统会防止重复报工。' : error.message;
      feedback.scrollIntoView({block:'nearest'});
    } finally {
      pending--; activity(); delete form.dataset.submitting; form.removeAttribute('aria-busy');
      buttons.forEach((button, index) => button.disabled = disabled[index]);
    }
  });
  window.addEventListener('pageshow', event => {
    if (event.persisted && document.body.dataset.role === 'employee') {
      document.querySelector('main')?.replaceChildren(); location.replace('/login');
    }
  });
  document.addEventListener('htmx:afterSwap', activity);
  document.addEventListener('change', async event => {
    if (event.target.id === 'csv-file' && event.target.files[0]) document.getElementById('csv-text').value = await event.target.files[0].text();
    if (event.target.id === 'dispatch-order') {
      const product = event.target.selectedOptions[0].dataset.product;
      const select = document.getElementById('dispatch-operation');
      for (const option of select.options) option.hidden = option.dataset.product !== product;
      select.value = [...select.options].find(option => !option.hidden)?.value || '';
    }
  });
})();
