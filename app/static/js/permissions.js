// Права текущего пользователя для JS, который рисует кнопки динамически
// (вложения, строки платежей и т.п.). Источник — #userPermissionsData в
// базовом layout (context processor inject_user_permissions). Это только
// UI-подсказка: сервер проверяет каждое действие сам (permission_required).
(function () {
  var perms = null;

  function load() {
    if (perms) return perms;
    perms = {};
    try {
      var el = document.getElementById("userPermissionsData");
      var list = el ? JSON.parse(el.textContent || "[]") : [];
      if (Array.isArray(list)) {
        for (var i = 0; i < list.length; i++) perms[String(list[i])] = true;
      }
    } catch (e) {
      perms = {};
    }
    return perms;
  }

  window.roobicoHasPermission = function (key) {
    return !!load()[String(key || "")];
  };

  window.roobicoHasAnyPermission = function () {
    for (var i = 0; i < arguments.length; i++) {
      if (window.roobicoHasPermission(arguments[i])) return true;
    }
    return false;
  };
})();
