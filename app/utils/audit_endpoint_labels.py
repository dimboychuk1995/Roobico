"""
Человеческие названия мутирующих эндпоинтов для технических строк журнала.

Когда хендлер не записал ни одного бизнес-события (audit_log.log_event),
after_request-хук (reports/audit/journal.py) пишет строку «технического»
события — и берёт подпись отсюда, чтобы в журнале было «Saved PDF design»,
а не «POST /settings/api/pdf-design». Значение: (action, entity_type,
summary). Эндпоинты, которых здесь нет, показываются как «METHOD path».
"""
from __future__ import annotations

ENDPOINT_LABELS: dict[str, tuple[str, str, str]] = {
    # ── auth / session ──
    "auth.login": ("login", "session", "Sign-in attempt"),
    "auth.forgot_password": ("send", "session", "Password reset requested"),
    "auth.reset_password": ("update", "session", "Password reset"),
    "auth.resend_verification": ("send", "session", "Verification e-mail re-sent"),
    "auth.accept_invite": ("create", "session", "Invitation accepted (password set)"),
    "main.set_active_shop": ("update", "session", "Active location switched"),
    "main.save_table_prefs": ("settings", "settings", "Table preferences saved"),
    "mobile_api.mobile_login": ("login", "session", "Mobile sign-in"),
    "mobile_api.mobile_logout": ("logout", "session", "Mobile sign-out"),
    "mobile_api.mobile_register_push_token": ("update", "session", "Mobile push token registered"),
    "mobile_api.mobile_set_active_shop": ("update", "session", "Active location switched (mobile)"),
    "mobile_api.mobile_vin_resolve": ("request", "unit", "VIN resolved (mobile)"),
    "tenant.register_tenant": ("create", "settings", "Company registered"),
    "assistant.chat": ("request", "other", "AI assistant chat"),
    # ── work orders (fallbacks; the handlers normally log richer events) ──
    "work_orders.api_parse_handwritten_work_order": ("request", "work_order", "AI scan of a handwritten work order"),
    "work_orders.api_polish_issue_description": ("request", "work_order", "AI polished an issue description"),
    "work_orders.api_preview_annual_inspection_pdf": ("request", "inspection", "Annual inspection PDF previewed"),
    "work_orders.api_update_email_contact": ("update", "customer", "Customer e-mail contact edited"),
    "work_orders.api_delete_email_contact": ("delete", "customer", "Customer e-mail contact removed"),
    "work_orders.api_save_labor_issue_description": ("update", "work_order", "Labor issue description saved"),
    # ── parts (fallbacks) ──
    "parts.parts_api_orders_parse_invoice": ("request", "parts_order", "AI parsed a vendor invoice"),
    "parts.parts_api_cross_refs_add": ("update", "part", "Cross reference added"),
    "parts.parts_api_cross_refs_remove": ("update", "part", "Cross reference removed"),
    # ── settings ──
    "main.settings_organization_save": ("settings", "settings", "Organization settings saved"),
    "settings.api_pdf_design_save": ("settings", "settings", "PDF design saved"),
    "settings.parts_settings_save_tax": ("settings", "settings", "Sales tax rate saved"),
    "settings.parts_categories_create": ("create", "settings", "Part category created"),
    "settings.parts_categories_update": ("update", "settings", "Part category updated"),
    "settings.parts_categories_delete": ("delete", "settings", "Part category deleted"),
    "settings.parts_locations_create": ("create", "settings", "Part location created"),
    "settings.parts_locations_update": ("update", "settings", "Part location updated"),
    "settings.parts_locations_delete": ("delete", "settings", "Part location deleted"),
    "settings.parts_pricing_rules_create": ("create", "settings", "Pricing matrix created"),
    "settings.parts_pricing_rules_save": ("update", "settings", "Pricing matrix saved"),
    "settings.parts_pricing_rules_delete": ("delete", "settings", "Pricing matrix deleted"),
    "settings.parts_pricing_rules_set_default": ("update", "settings", "Default pricing matrix changed"),
    "settings.work_orders_index": ("settings", "settings", "Work order settings saved"),
    "settings.work_orders_labor_rates_create": ("create", "labor_rate", "Labor rate created"),
    "settings.work_orders_labor_rates_update": ("update", "labor_rate", "Labor rate updated"),
    "settings.work_orders_labor_rates_delete": ("delete", "labor_rate", "Labor rate deleted"),
    "settings.wo_presets_create": ("create", "preset", "WO preset created"),
    "settings.wo_presets_update": ("update", "preset", "WO preset updated"),
    "settings.wo_presets_delete": ("delete", "preset", "WO preset deleted"),
    "settings.locations_index": ("create", "location", "Location created"),
    "settings.api_locations_create": ("create", "location", "Location created"),
    "settings.api_locations_update": ("update", "location", "Location updated"),
    "settings.api_locations_inactive": ("deactivate", "location", "Location deactivated"),
    "settings.integrations_save": ("settings", "integration", "Integration settings saved"),
    "settings.integrations_delete": ("delete", "integration", "Integration disconnected"),
    "settings.integrations_test": ("request", "integration", "Integration connection tested"),
    "settings.integrations_uattend_employees_selection": ("settings", "integration", "uAttend employee selection saved"),
    "settings.integrations_email_orders_save": ("settings", "integration", "Email orders inbox settings saved"),
    "settings.integrations_email_orders_rotate": ("settings", "integration", "Email orders inbox address rotated"),
    "settings.integrations_email_orders_process": ("create", "parts_order", "Inbound e-mail processed into orders"),
    "settings.integrations_email_orders_ignore": ("update", "integration", "Inbound e-mail ignored"),
    "settings.users_index": ("create", "user", "User invited"),
    "settings.users_edit": ("update", "user", "User updated"),
    "settings.users_deactivate": ("deactivate", "user", "User deactivated"),
    "settings.users_activate": ("activate", "user", "User activated"),
    "settings.users_resend_invite": ("send", "user", "Invitation re-sent"),
    "settings.api_roles_create": ("create", "role", "Role created"),
    "settings.api_roles_update": ("update", "role", "Role updated"),
    "settings.api_roles_delete": ("delete", "role", "Role deleted"),
    "settings.api_roles_clone": ("create", "role", "Role cloned"),
    "settings.api_user_permissions_update": ("permissions", "user", "Per-user permission overrides changed"),
    "dashboard.dashboard_goals_save_api": ("settings", "settings", "Dashboard goals saved"),
    "calendar.api_save_statuses": ("settings", "calendar_event", "Appointment statuses saved"),
    # ── billing ──
    "billing.set_billing_period": ("settings", "billing", "Billing period changed"),
    "billing.pay_now": ("payment", "billing", "Subscription invoice paid"),
    "billing.setup_card": ("update", "billing", "Payment card setup started"),
    "billing.stripe_webhook": ("request", "billing", "Stripe webhook"),
    # ── import / export ──
    "import_export.upload_headers": ("request", "import", "Import file uploaded (headers read)"),
    "import_export.run_import": ("import", "import", "Data import executed"),
    # ── inbound e-mail ──
    "inbound_email.inbound_email_webhook": ("request", "parts_order", "Inbound e-mail received"),
    # ── attachments (fallbacks) ──
    "attachments.api_upload": ("upload", "attachment", "Attachment uploaded"),
    "attachments.api_delete": ("delete", "attachment", "Attachment deleted"),
    # ── customer portal ──
    "customer_portal.api_send_portal_link": ("send", "customer", "Customer portal link sent"),
    # ── admin panel (superadmin) ──
    "admin_panel.login": ("login", "session", "Admin panel sign-in"),
    "admin_panel.tenant_set_discount": ("settings", "billing", "Admin: tenant discount set"),
    "admin_panel.tenant_set_billing_period": ("settings", "billing", "Admin: billing period set"),
    "admin_panel.tenant_toggle_active": ("update", "settings", "Admin: tenant active toggled"),
    "admin_panel.tenant_extend_subscription": ("settings", "billing", "Admin: subscription extended"),
    "admin_panel.tenant_clear_subscription": ("settings", "billing", "Admin: subscription cleared"),
    "admin_panel.shop_toggle_active": ("update", "location", "Admin: location active toggled"),
    "admin_panel.user_toggle_active": ("update", "user", "Admin: user active toggled"),
    "billing.admin_send_invoice": ("send", "billing", "Admin: invoice sent"),
    "billing.admin_charge_now": ("payment", "billing", "Admin: charged now"),
}


def describe_endpoint(endpoint: str, method: str, path: str) -> tuple[str, str, str]:
    """(action, entity_type, summary) для технической строки журнала."""
    found = ENDPOINT_LABELS.get(str(endpoint or ""))
    if found:
        return found
    return ("request", "request", f"{str(method or '').upper()} {path}")
