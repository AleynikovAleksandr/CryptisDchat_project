// Вход в админ-панель: показать/скрыть пароль и блокировка повторной отправки.
// Отдельный файл, а не inline-скрипт: CSP панели — default-src 'self'.
(function () {
  var form = document.getElementById('admin-login');
  if (!form) return;
  var input = form.querySelector('#password');
  var eye = form.querySelector('.eye-btn');

  if (input && eye) {
    eye.hidden = false;  // без JS кнопка бесполезна — показываем только когда она работает
    eye.addEventListener('click', function () {
      var show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      eye.setAttribute('aria-pressed', String(show));
      eye.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
      input.focus();
    });
  }

  form.addEventListener('submit', function () {
    var btn = form.querySelector('.submit-btn');
    if (input && input.type === 'text') input.type = 'password';
    if (btn) setTimeout(function () { btn.disabled = true; }, 0);
  });
})();
