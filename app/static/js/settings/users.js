(function () {
  var root = document.body;
  if (!root || root.dataset.settingsUsersClickBound === "1") {
    return;
  }
  root.dataset.settingsUsersClickBound = "1";

  // ── Pay-type toggle (works for any modal containing the data-pay-type-select) ──
  function applyPayType(scope) {
    var sel = scope.querySelector("[data-pay-type-select]");
    if (!sel) return;
    var salaryField = scope.querySelector("[data-salary-field]");
    var hourlyInfo = scope.querySelector("[data-hourly-info]");
    var isHourly = sel.value === "hourly";
    if (salaryField) salaryField.classList.toggle("d-none", isHourly);
    if (hourlyInfo) hourlyInfo.classList.toggle("d-none", !isHourly);
    var salaryInput = scope.querySelector("[data-salary-field] input[name='salary_amount']");
    if (salaryInput) {
      if (isHourly) {
        salaryInput.value = "";
        salaryInput.removeAttribute("required");
      }
    }
  }
  document.querySelectorAll("[data-pay-type-select]").forEach(function (sel) {
    var modal = sel.closest(".modal") || document;
    sel.addEventListener("change", function () { applyPayType(modal); });
    applyPayType(modal);
  });

  // ── Invite toggle: с приглашением поля пароля скрыты и не обязательны ──
  var inviteCheck = document.getElementById("send_invite");
  if (inviteCheck) {
    var applyInviteMode = function () {
      var invite = inviteCheck.checked;
      document.querySelectorAll("[data-manual-password-field]").forEach(function (el) {
        el.classList.toggle("d-none", invite);
        var input = el.querySelector("input");
        if (input) {
          if (invite) {
            input.value = "";
            input.removeAttribute("required");
          } else {
            input.setAttribute("required", "required");
          }
        }
      });
      var submitBtn = document.getElementById("createUserSubmitBtn");
      if (submitBtn) submitBtn.textContent = invite ? "Send invitation" : "Create user";
    };
    inviteCheck.addEventListener("change", applyInviteMode);
    applyInviteMode();
  }

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
    var payTypeInput = editForm.querySelector("#edit_pay_type");
    var salaryInput = editForm.querySelector("#edit_salary_amount");

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

    if (payTypeInput) {
      payTypeInput.value = btn.getAttribute("data-pay-type") || "salary";
    }
    if (salaryInput) {
      salaryInput.value = btn.getAttribute("data-salary-amount") || "";
    }
    applyPayType(editForm);

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
