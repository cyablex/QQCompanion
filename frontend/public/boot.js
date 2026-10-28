(() => {
  const panel = document.getElementById('boot-status');
  const detail = document.getElementById('boot-detail');
  const retry = document.getElementById('boot-retry');
  let mounted = false;
  function failed() {
    if (mounted) return;
    detail.textContent = '页面未能载入，请重新载入。后台聊天仍可继续运行。';
    retry.hidden = false;
  }
  retry.addEventListener('click', () => location.reload());
  window.addEventListener('error', failed, true);
  window.addEventListener('unhandledrejection', failed);
  const timer = setTimeout(failed, 15000);
  window.addEventListener('dafeiyu:mounted', () => {
    mounted = true;
    clearTimeout(timer);
    panel.remove();
  }, { once: true });
})();
