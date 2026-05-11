from __future__ import annotations


# =========================================================
# 1) ЕДИНЫЙ КАТАЛОГ PERMISSIONS (источник истины)
#    Формат ключей: <module>.<action>
# =========================================================
#
# Группировка дана в PERMISSION_GROUPS ниже — она используется UI
# (tree view) и при сидинге дефолтных ролей.
# =========================================================
PERMISSIONS: dict[str, str] = {
    # ── Dashboard ───────────────────────────────────────────
    "dashboard.view": "View dashboard",
    "dashboard.edit_goals": "Edit dashboard goals",

    # ── Calendar ────────────────────────────────────────────
    "calendar.view": "View calendar",
    "calendar.create": "Create appointments",
    "calendar.edit": "Edit appointments",
    "calendar.delete": "Delete appointments",
    "calendar.manage_settings": "Manage calendar settings (statuses/presets)",

    # ── Customers ───────────────────────────────────────────
    "customers.view": "View customers list & details",
    "customers.create": "Create customers",
    "customers.edit": "Edit customers",
    "customers.deactivate": "Deactivate customers",
    "customers.manage_units": "Create/edit customer units",
    "customers.manage_portal": "Manage customer portal links",

    # ── Vendors ─────────────────────────────────────────────
    "vendors.view": "View vendors",
    "vendors.create": "Create vendors",
    "vendors.edit": "Edit vendors",
    "vendors.deactivate": "Deactivate vendors",

    # ── Parts (catalog / inventory) ─────────────────────────
    "parts.view": "View parts catalog",
    "parts.create": "Create parts",
    "parts.edit": "Edit parts",
    "parts.delete": "Delete parts",
    "parts.adjust_stock": "Adjust part stock manually",
    "parts.view_costs": "View part costs (avg cost / cost columns)",

    # ── Parts Orders ────────────────────────────────────────
    "parts_orders.view": "View parts orders",
    "parts_orders.create": "Create parts orders",
    "parts_orders.edit": "Edit parts orders",
    "parts_orders.delete": "Delete parts orders",
    "parts_orders.receive": "Receive / un-receive parts orders",
    "parts_orders.manage_payments": "Record / delete parts order payments",

    # ── Work Orders ─────────────────────────────────────────
    "work_orders.view": "View work orders & estimates",
    "work_orders.create": "Create work orders",
    "work_orders.edit": "Edit work orders",
    "work_orders.change_status": "Change work order status",
    "work_orders.delete": "Delete work orders",
    "work_orders.manage_payments": "Record / delete work order payments",
    "work_orders.send_to_customer": "Send work order / authorization to customer",
    "work_orders.export_pdf": "Export work order PDF",
    "work_orders.view_costs": "View part costs inside WO",

    # ── Attachments ─────────────────────────────────────────
    "attachments.view": "View attachments",
    "attachments.upload": "Upload attachments",
    "attachments.delete": "Delete attachments",

    # ── Reports ─────────────────────────────────────────────
    "reports.view": "View reports",
    "reports.export": "Export reports (Excel/PDF)",
    "reports.view_audit": "View audit journal",

    # ── Import / Export ─────────────────────────────────────
    "import_export.view": "Open Import / Export tool",
    "import_export.import": "Run data imports",
    "import_export.export": "Run data exports",

    # ── Settings ────────────────────────────────────────────
    "settings.view": "View settings index",
    "settings.manage_org": "Manage organization & general settings",
    "settings.manage_locations": "Manage shops / locations",
    "settings.manage_users": "Manage users",
    "settings.manage_roles": "Manage roles & permissions",
    "settings.manage_parts_settings": "Manage parts categories / locations / pricing",
    "settings.manage_wo_presets": "Manage work order presets",
    "settings.manage_wo_settings": "Manage work order settings (labor rates / supply / cores)",
    "settings.manage_workflows": "Manage workflows",
    "settings.manage_notifications": "Manage notification settings",
    "settings.manage_pdf_design": "Manage PDF design",
}

ALL_PERMISSIONS: list[str] = sorted(PERMISSIONS.keys())


# =========================================================
# 2) ГРУППИРОВКА ДЛЯ UI (tree view)
#    Каждый блок: { key, label, items: [perm_key, ...] }
#    Порядок групп — порядок их отображения на странице.
# =========================================================
PERMISSION_GROUPS: list[dict] = [
    {
        "key": "dashboard",
        "label": "Dashboard",
        "items": ["dashboard.view", "dashboard.edit_goals"],
    },
    {
        "key": "calendar",
        "label": "Calendar",
        "items": [
            "calendar.view",
            "calendar.create",
            "calendar.edit",
            "calendar.delete",
            "calendar.manage_settings",
        ],
    },
    {
        "key": "customers",
        "label": "Customers",
        "items": [
            "customers.view",
            "customers.create",
            "customers.edit",
            "customers.deactivate",
            "customers.manage_units",
            "customers.manage_portal",
        ],
    },
    {
        "key": "vendors",
        "label": "Vendors",
        "items": [
            "vendors.view",
            "vendors.create",
            "vendors.edit",
            "vendors.deactivate",
        ],
    },
    {
        "key": "parts",
        "label": "Parts (catalog & inventory)",
        "items": [
            "parts.view",
            "parts.create",
            "parts.edit",
            "parts.delete",
            "parts.adjust_stock",
            "parts.view_costs",
        ],
    },
    {
        "key": "parts_orders",
        "label": "Parts Orders",
        "items": [
            "parts_orders.view",
            "parts_orders.create",
            "parts_orders.edit",
            "parts_orders.delete",
            "parts_orders.receive",
            "parts_orders.manage_payments",
        ],
    },
    {
        "key": "work_orders",
        "label": "Work Orders",
        "items": [
            "work_orders.view",
            "work_orders.create",
            "work_orders.edit",
            "work_orders.change_status",
            "work_orders.delete",
            "work_orders.manage_payments",
            "work_orders.send_to_customer",
            "work_orders.export_pdf",
            "work_orders.view_costs",
        ],
    },
    {
        "key": "attachments",
        "label": "Attachments",
        "items": [
            "attachments.view",
            "attachments.upload",
            "attachments.delete",
        ],
    },
    {
        "key": "reports",
        "label": "Reports",
        "items": ["reports.view", "reports.export", "reports.view_audit"],
    },
    {
        "key": "import_export",
        "label": "Import / Export",
        "items": [
            "import_export.view",
            "import_export.import",
            "import_export.export",
        ],
    },
    {
        "key": "settings",
        "label": "Settings",
        "items": [
            "settings.view",
            "settings.manage_org",
            "settings.manage_locations",
            "settings.manage_users",
            "settings.manage_roles",
            "settings.manage_parts_settings",
            "settings.manage_wo_presets",
            "settings.manage_wo_settings",
            "settings.manage_workflows",
            "settings.manage_notifications",
            "settings.manage_pdf_design",
        ],
    },
]


def _all() -> set[str]:
    return set(ALL_PERMISSIONS)


def _safe_subset(keys: set[str]) -> set[str]:
    """
    На случай опечатки: гарантируем, что роли не содержат ключей,
    которых нет в каталоге PERMISSIONS.
    """
    allp = _all()
    return {k for k in keys if k in allp}


# Системные роли, которые нельзя удалять или переименовывать.
# Owner — особо защищён (его нельзя редактировать вообще, у него всегда все права).
SYSTEM_ROLE_KEYS: set[str] = {
    "owner",
    "general_manager",
    "manager",
    "parts_manager",
    "senior_mechanic",
    "mechanic",
    "viewer",
}

PROTECTED_ROLE_KEYS: set[str] = {"owner"}


# =========================================================
# 3) ДЕФОЛТНЫЕ РОЛИ (seed в tenant DB при создании tenant)
# =========================================================
def build_default_roles() -> list[dict]:
    allp = _all()

    # Полный доступ
    owner = allp
    general_manager = allp

    # Manager: всё кроме управления ролями
    manager = allp - {"settings.manage_roles"}

    # Parts manager
    parts_manager = _safe_subset({
        "dashboard.view",
        "calendar.view",
        "customers.view",
        "vendors.view",
        "vendors.create",
        "vendors.edit",
        "parts.view",
        "parts.create",
        "parts.edit",
        "parts.delete",
        "parts.adjust_stock",
        "parts.view_costs",
        "parts_orders.view",
        "parts_orders.create",
        "parts_orders.edit",
        "parts_orders.delete",
        "parts_orders.receive",
        "parts_orders.manage_payments",
        "work_orders.view",
        "attachments.view",
        "attachments.upload",
        "reports.view",
        "settings.view",
        "settings.manage_parts_settings",
    })

    # Senior mechanic
    senior_mechanic = _safe_subset({
        "dashboard.view",
        "calendar.view",
        "calendar.create",
        "calendar.edit",
        "customers.view",
        "vendors.view",
        "parts.view",
        "parts_orders.view",
        "work_orders.view",
        "work_orders.create",
        "work_orders.edit",
        "work_orders.change_status",
        "work_orders.export_pdf",
        "attachments.view",
        "attachments.upload",
        "reports.view",
    })

    # Mechanic — только просмотр своих WO
    mechanic = _safe_subset({
        "calendar.view",
        "work_orders.view",
        "attachments.view",
    })

    # Viewer — read-only офис/аудит
    viewer = _safe_subset({
        "dashboard.view",
        "calendar.view",
        "customers.view",
        "vendors.view",
        "parts.view",
        "parts_orders.view",
        "work_orders.view",
        "attachments.view",
        "reports.view",
        "reports.view_audit",
        "settings.view",
    })

    return [
        {"key": "owner", "name": "Owner", "permissions": sorted(owner), "is_system": True, "is_protected": True},
        {"key": "general_manager", "name": "General manager", "permissions": sorted(general_manager), "is_system": True},
        {"key": "manager", "name": "Manager", "permissions": sorted(manager), "is_system": True},
        {"key": "parts_manager", "name": "Parts manager", "permissions": sorted(parts_manager), "is_system": True},
        {"key": "senior_mechanic", "name": "Senior mechanic", "permissions": sorted(senior_mechanic), "is_system": True},
        {"key": "mechanic", "name": "Mechanic", "permissions": sorted(mechanic), "is_system": True},
        {"key": "viewer", "name": "Viewer", "permissions": sorted(viewer), "is_system": True},
    ]
