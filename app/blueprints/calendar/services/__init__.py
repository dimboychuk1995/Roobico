"""
Сервисный слой календаря:

  statuses.py    — статусы записей и рабочие часы (calendar_settings);
  events.py      — записи: лейблы, бригада, даты, сериализация, сборка
                   документа из POST/PUT;
  work_orders.py — связь записей с work orders: снимок WO, живые данные у
                   привязанных записей и хуки, которые зовут другие модули
                   (sync_linked_appointments_status, link_event_to_work_order,
                   unlink_work_order_events, refresh_linked_appointments_identity,
                   linked_appointments_for_wo).
"""
