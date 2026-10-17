/* Mostrar u ocultar la contraseña (el «ojito»). Mejora progresiva: sin JavaScript la contraseña sigue oculta.
   Sirve para cualquier campo: basta que el botón lleve data-password-toggle="<id del campo>" (macro password_toggle). */
(function () {
  'use strict';

  function setVisible(button, input, visible) {
    var start = null, end = null;
    try { start = input.selectionStart; end = input.selectionEnd; } catch (e) { /* algunos navegadores no lo permiten */ }
    input.type = visible ? 'text' : 'password';
    button.setAttribute('aria-pressed', visible ? 'true' : 'false');   // el estado lo anuncia el lector de pantalla; la etiqueta no cambia
    var icon = button.querySelector('[data-password-icon]');
    if (icon) icon.textContent = visible ? 'visibility_off' : 'visibility';
    if (start !== null) { try { input.setSelectionRange(start, end); } catch (e) { /* sin selección que conservar */ } }
  }

  function inputOf(button) {
    return document.getElementById(button.getAttribute('data-password-toggle'));
  }

  document.addEventListener('click', function (event) {
    var button = event.target.closest && event.target.closest('[data-password-toggle]');
    var input = button && inputOf(button);
    if (input) setVisible(button, input, input.type === 'password');
  });

  // Al salir de la página, o al volver con «atrás» desde la caché del navegador, ninguna contraseña queda a la vista.
  window.addEventListener('pagehide', function () {
    document.querySelectorAll('[data-password-toggle][aria-pressed="true"]').forEach(function (button) {
      var input = inputOf(button);
      if (input) setVisible(button, input, false);
    });
  });
})();
