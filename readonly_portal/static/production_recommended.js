// Mantener el plan actualizado sin perder filtros o simulaciones en edición.
(() => {
  let unsentChanges = false;
  document.querySelectorAll('.recommended-page form').forEach(form => {
    form.addEventListener('input', () => { unsentChanges = true; });
    form.addEventListener('change', () => { unsentChanges = true; });
  });
  window.setInterval(() => {
    if (!unsentChanges && document.visibilityState === 'visible') window.location.reload();
  }, 60000);
})();
