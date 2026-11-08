(() => {
  if (window.workshopInitialized) return;
  window.workshopInitialized = true;
  let lastActivity = Date.now(), lastTouch = 0, pending = 0;
  const clocks = new WeakMap();
  const anchorClocks = () => document.querySelectorAll('[data-elapsed-us]').forEach(label => {
    if (!clocks.has(label)) clocks.set(label, {at:performance.now(), serverUs:Number(label.dataset.anchorUs), raw:Number(label.dataset.elapsedUs)});
  });
  anchorClocks();
  const activity = () => {
    lastActivity = Date.now();
    if (document.body.dataset.role !== 'employee' || lastActivity - lastTouch < 2000) return;
    lastTouch = lastActivity;
    fetch('/session/touch', {method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: new URLSearchParams({csrf: document.body.dataset.csrf})}).catch(() => {});
  };
  ['pointerdown', 'keydown', 'input'].forEach(event => document.addEventListener(event, activity));
  setInterval(() => {
    anchorClocks();
    for (const label of document.querySelectorAll('[data-elapsed-us]')) { const clock = clocks.get(label); const serverNow = clock.serverUs + (performance.now() - clock.at) * 1000; const elapsed = clock.raw + (serverNow - clock.serverUs) * Number(label.dataset.activeMembers); label.textContent = (elapsed / 60000000).toFixed(2); }
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
    feedback.id = 'feedback-' + body.get('request_id');
    form.querySelectorAll('[aria-invalid]').forEach(input => {input.removeAttribute('aria-invalid'); input.removeAttribute('aria-describedby');});
    feedback.textContent = '正在保存，请稍候…';
    pending++; activity();
    try {
      const response = await fetch(action, {method: 'POST', headers: {'Accept':'application/json'}, body, signal:AbortSignal.timeout(35000)});
      if (response.redirected) { location.replace(response.url); return; }
      const result = await response.json();
      if (!response.ok || result.error) {
        const input = result.field && form.elements.namedItem(result.field);
        if (input instanceof HTMLElement) {input.setAttribute('aria-invalid','true');input.setAttribute('aria-describedby',feedback.id); input.focus();}
        throw new Error(result.error || '保存失败，请重试');
      }
      const current = form.closest('[data-dispatch]');
      if (result.html && current) {
        const doc = new DOMParser().parseFromString(result.html, 'text/html');
        const card = doc.querySelector('[data-dispatch]');
        if (card && card.dataset.dispatch === current.dataset.dispatch) {
          current.replaceWith(card);
          anchorClocks();
          const field = card.querySelector('[name=good]');
          if (field) {field.focus({preventScroll:true}); field.select();}
          return;
        }
      }
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
  // Rendering and clock ticks are not user activity. Only physical input and an active submission touch the session.
  const preview = () => {
    const form = document.getElementById('bulk-dispatch'); if (!form) return;
    const selected = [...form.querySelectorAll('[name=employees]:checked')];
    const search = form.querySelector('#bulk-employee-search').value.trim().toLowerCase();
    for (const row of form.querySelectorAll('.bulk-employee')) row.hidden = !row.textContent.toLowerCase().includes(search) && !row.querySelector('[name=employees]').checked;
    let total = 0n, valid = true;
    const qty = value => {if (!/^\d+(\.\d{1,4})?$/.test(value)) return null;const [whole,frac=''] = value.split('.'); return BigInt(whole)*10000n+BigInt(frac.padEnd(4,'0'));};
    const show = value => {const sign=value<0n?'-':'';value=value<0n?-value:value;return `${sign}${value/10000n}.${(value%10000n).toString().padStart(4,'0')}`;};
    for (const box of form.querySelectorAll('[name=employees]')) {
      const input = form.elements.namedItem('quantity_'+box.value); input.required = box.checked;
      if (box.checked) {const value = qty(input.value); if (value===null || value<=0n) valid=false;else total+=value;}
    }
    const plan = [...document.querySelectorAll('#dispatch-plans span')].find(p => p.dataset.order===form.elements.order_id.value && p.dataset.operation===form.elements.operation_id.value);
    const label = document.getElementById('bulk-preview');
    label.textContent = `已选 ${selected.length} 人 · 独立任务合计 ${show(total)} 工序单位` + (!valid?'；请填写每位所选员工的正数量。':'。') + (plan?` 工序计划 ${show(BigInt(plan.dataset.plan))}，可派 ${show(BigInt(plan.dataset.remaining))}，本次后剩余 ${show(BigInt(plan.dataset.remaining)-total)}。${plan.dataset.frozen==='true'?'沿用冻结版本。':'首次派工将冻结工艺。'}`:' 请先选择工单和工序。') + ' 保存时再次校验；整批成功或整批不保存。';
    label.classList.toggle('alert-warning', !!plan && total>BigInt(plan.dataset.remaining));
  };
  document.addEventListener('input', preview);
  document.addEventListener('htmx:afterSwap', () => {anchorClocks();preview();});
  document.addEventListener('change', async event => {
    if (event.target.id === 'csv-file' && event.target.files[0]) document.getElementById('csv-text').value = await event.target.files[0].text();
    if (event.target.id === 'dispatch-order' || event.target.id === 'bulk-order') {
      const product = event.target.selectedOptions[0].dataset.product;
      const select = document.getElementById(event.target.id==='bulk-order'?'bulk-operation':'dispatch-operation');
      for (const option of select.options) {option.hidden = option.dataset.product !== product;option.disabled=option.hidden;}
      select.value = [...select.options].find(option => !option.hidden)?.value || '';
    }
    preview();
  });
  preview();
})();
