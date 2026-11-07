(() => {
  if (window.workshopInitialized) return;
  window.workshopInitialized = true;
  let lastActivity = Date.now(), lastTouch = 0;
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
    const remaining = Math.max(0, 15 - Math.floor((Date.now() - lastActivity) / 1000));
    const label = document.getElementById('idle-count'); if (label) label.textContent = remaining;
    if (remaining === 0) {
      document.body.dataset.role = 'guest';
      // Clear the visible identity even when the network is unavailable; the server independently enforces idle expiry.
      const main = document.querySelector('main'); if (main) main.replaceChildren(Object.assign(document.createElement('p'), {textContent: '身份已退出，后台生产计时继续。'}));
      fetch('/logout', {method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: new URLSearchParams({csrf: document.body.dataset.csrf})}).finally(() => location.replace('/login'));
    }
  }, 1000);
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
