(function () {
  var root = document.body;
  if (!root || root.dataset.settingsUsersClickBound === "1") {
    return;
  }
  root.dataset.settingsUsersClickBound = "1";

  document.addEventListener("click", function (event) {
    var btn = event.target && event.target.closest ? event.target.closest(".edit-user-btn") : null;
    if (!btn) return;

    var editForm = document.getElementById("editUserForm");
    if (!editForm) return;

    var firstNameInput = editForm.querySelector("#edit_user_first_name");
    var lastNameInput = editForm.querySelector("#edit_user_last_name");
    var emailInput = editForm.querySelector("#edit_user_email");
    var phoneInput = editForm.querySelector("#edit_user_phone");
    var roleInput = editForm.querySelector("#edit_user_role");
    var isActiveInput = editForm.querySelector("#edit_user_is_active");
    var passwordInput = editForm.querySelector("#edit_user_password");

    if (!firstNameInput || !lastNameInput || !emailInput || !phoneInput || !roleInput || !isActiveInput) {
      return;
    }

    var userId = btn.getAttribute("data-user-id") || "";
    if (!userId) return;

    editForm.setAttribute("action", "/settings/users/" + encodeURIComponent(userId) + "/edit");

    firstNameInput.value = btn.getAttribute("data-first-name") || "";
    lastNameInput.value = btn.getAttribute("data-last-name") || "";
    emailInput.value = btn.getAttribute("data-email") || "";
    phoneInput.value = btn.getAttribute("data-phone") || "";
    roleInput.value = btn.getAttribute("data-role") || "viewer";
    isActiveInput.checked = (btn.getAttribute("data-is-active") || "1") === "1";

    var rawShopIds = (btn.getAttribute("data-shop-ids") || "").split(",");
    var selectedShopIds = {};
    for (var i = 0; i < rawShopIds.length; i += 1) {
      var sid = String(rawShopIds[i] || "").trim();
      if (!sid) continue;
      selectedShopIds[sid] = true;
    }

    var boxes = editForm.querySelectorAll(".edit-user-shop-checkbox");
    for (var j = 0; j < boxes.length; j += 1) {
      var box = boxes[j];
      box.checked = !!selectedShopIds[String(box.value || "")];
    }

    if (passwordInput) {
      passwordInput.value = "";
    }
  });
})();
