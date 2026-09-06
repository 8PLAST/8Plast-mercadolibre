document.getElementById('refresh')?.addEventListener('click',()=>location.reload());
const stamp=document.getElementById('updated');if(stamp)stamp.textContent='Actualizado '+new Date().toLocaleString('es-AR');
// Mantiene la vista remota alineada con el ciclo seguro del trabajador de Mercado Libre.
window.setInterval(()=>location.reload(),300000);
