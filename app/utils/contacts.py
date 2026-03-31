from __future__ import annotations


def _clean_string(value) -> str:
    return str(value or "").strip()


def _clean_email(value) -> str:
    return _clean_string(value).lower()


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return _clean_string(value).lower() in {"1", "true", "yes", "on"}


def contact_full_name(contact: dict | None) -> str:
    if not isinstance(contact, dict):
        return ""
    first_name = _clean_string(contact.get("first_name"))
    last_name = _clean_string(contact.get("last_name"))
    return f"{first_name} {last_name}".strip()


def has_contact_name(contacts: list[dict] | None) -> bool:
    if not isinstance(contacts, list):
        return False
    return any(contact_full_name(contact) for contact in contacts if isinstance(contact, dict))


def normalize_contacts(raw_contacts) -> list[dict]:
    if not isinstance(raw_contacts, list):
        return []

    out: list[dict] = []
    for raw in raw_contacts:
        if not isinstance(raw, dict):
            continue

        contact = {
            "first_name": _clean_string(raw.get("first_name")),
            "last_name": _clean_string(raw.get("last_name")),
            "phone": _clean_string(raw.get("phone")),
            "email": _clean_email(raw.get("email")),
            "is_main": _as_bool(raw.get("is_main")),
        }

        has_data = any(
            [
                contact["first_name"],
                contact["last_name"],
                contact["phone"],
                contact["email"],
            ]
        )
        if not has_data:
            continue

        out.append(contact)

    if not out:
        return []

    main_index = next((index for index, contact in enumerate(out) if contact.get("is_main")), 0)
    for index, contact in enumerate(out):
        contact["is_main"] = index == main_index

    return out


def _legacy_contact_from_entity(entity: dict | None, entity_type: str | None = None) -> dict | None:
    if not isinstance(entity, dict):
        return None

    if entity_type == "vendor":
        first_name = _clean_string(entity.get("primary_contact_first_name"))
        last_name = _clean_string(entity.get("primary_contact_last_name"))
    else:
        first_name = _clean_string(entity.get("first_name"))
        last_name = _clean_string(entity.get("last_name"))

    phone = _clean_string(entity.get("phone"))
    email = _clean_email(entity.get("email"))

    if not any([first_name, last_name, phone, email]):
        return None

    return {
        "first_name": first_name,
        "last_name": last_name,
        "phone": phone,
        "email": email,
        "is_main": True,
    }


def get_contacts(entity: dict | None, entity_type: str | None = None) -> list[dict]:
    if not isinstance(entity, dict):
        return []

    contacts = normalize_contacts(entity.get("contacts"))
    if contacts:
        return contacts

    legacy_contact = _legacy_contact_from_entity(entity, entity_type=entity_type)
    if not legacy_contact:
        return []

    return [legacy_contact]


def get_main_contact(entity: dict | None, entity_type: str | None = None) -> dict | None:
    contacts = get_contacts(entity, entity_type=entity_type)
    if not contacts:
        return None
    return next((contact for contact in contacts if contact.get("is_main")), contacts[0])


def get_main_contact_name(entity: dict | None, entity_type: str | None = None) -> str:
    return contact_full_name(get_main_contact(entity, entity_type=entity_type))


def get_main_contact_phone(entity: dict | None, entity_type: str | None = None) -> str:
    contact = get_main_contact(entity, entity_type=entity_type)
    if isinstance(contact, dict):
        return _clean_string(contact.get("phone"))
    return ""


def get_main_contact_email(entity: dict | None, entity_type: str | None = None) -> str:
    contact = get_main_contact(entity, entity_type=entity_type)
    if isinstance(contact, dict):
        return _clean_email(contact.get("email"))
    return ""


def build_customer_legacy_contact_fields(contacts: list[dict] | None) -> dict:
    normalized = normalize_contacts(contacts or [])
    contact = next((item for item in normalized if item.get("is_main")), normalized[0] if normalized else {})
    return {
        "first_name": _clean_string(contact.get("first_name")) or None,
        "last_name": _clean_string(contact.get("last_name")) or None,
        "phone": _clean_string(contact.get("phone")) or None,
        "email": _clean_email(contact.get("email")) or None,
    }


def build_vendor_legacy_contact_fields(contacts: list[dict] | None) -> dict:
    normalized = normalize_contacts(contacts or [])
    contact = next((item for item in normalized if item.get("is_main")), normalized[0] if normalized else {})
    return {
        "primary_contact_first_name": _clean_string(contact.get("first_name")) or None,
        "primary_contact_last_name": _clean_string(contact.get("last_name")) or None,
        "phone": _clean_string(contact.get("phone")) or None,
        "email": _clean_email(contact.get("email")) or None,
    }


def build_contacts_from_form(form) -> list[dict]:
    first_names = form.getlist("contact_first_name")
    last_names = form.getlist("contact_last_name")
    phones = form.getlist("contact_phone")
    emails = form.getlist("contact_email")

    raw_main_index = _clean_string(form.get("contact_main_index"))
    main_index = None
    if raw_main_index:
        try:
            main_index = int(raw_main_index)
        except Exception:
            main_index = None

    total = max(len(first_names), len(last_names), len(phones), len(emails))
    raw_contacts = []
    for index in range(total):
        raw_contacts.append(
            {
                "first_name": first_names[index] if index < len(first_names) else "",
                "last_name": last_names[index] if index < len(last_names) else "",
                "phone": phones[index] if index < len(phones) else "",
                "email": emails[index] if index < len(emails) else "",
                "is_main": main_index == index,
            }
        )

    return normalize_contacts(raw_contacts)


def build_contacts_from_payload(data: dict | None) -> list[dict]:
    if not isinstance(data, dict):
        return []
    return normalize_contacts(data.get("contacts"))