import logging
from datetime import date, datetime, timezone
from app import db
from models import CustomerDeliveryDateOverride

logger = logging.getLogger(__name__)

REASON_CODES = [
    ("holiday", "Holiday / Bank Holiday"),
    ("weather", "Weather disruption"),
    ("logistics", "Logistics / Fleet issue"),
    ("customer_request", "Customer request"),
    ("route_change", "Route change"),
    ("other", "Other"),
]


def get_active_overrides_for_customers(customer_codes: list[str]) -> dict:
    if not customer_codes:
        return {}
    rows = (
        CustomerDeliveryDateOverride.query
        .filter(
            CustomerDeliveryDateOverride.customer_code_365.in_(customer_codes),
            CustomerDeliveryDateOverride.is_active.is_(True),
        )
        .all()
    )
    result = {}
    for r in rows:
        result.setdefault(r.customer_code_365, []).append(r)
    return result


def get_active_override_for_customer_date(customer_code: str, original_date: date):
    return (
        CustomerDeliveryDateOverride.query
        .filter_by(
            customer_code_365=customer_code,
            original_delivery_date=original_date,
            is_active=True,
        )
        .first()
    )


def resolve_effective_delivery(customer_code: str, natural_delivery_date: date,
                                active_overrides: list | None = None):
    if active_overrides is not None:
        for ov in active_overrides:
            if ov.original_delivery_date == natural_delivery_date and ov.is_active:
                return ov.override_delivery_date, ov
    else:
        ov = get_active_override_for_customer_date(customer_code, natural_delivery_date)
        if ov:
            return ov.override_delivery_date, ov
    return natural_delivery_date, None


def apply_delivery_overrides(overrides_data: list[dict], created_by: str | None = None) -> dict:
    created = 0
    updated = 0
    errors = []
    now = datetime.now(timezone.utc)

    for item in overrides_data:
        customer_code = item.get("customer_code_365")
        try:
            orig_date = date.fromisoformat(item["original_delivery_date"])
            new_date = date.fromisoformat(item["override_delivery_date"])
        except (ValueError, KeyError) as e:
            errors.append({"customer_code_365": customer_code, "error": str(e)})
            continue

        reason_code = item.get("reason_code", "other")
        reason_notes = item.get("reason_notes", "")

        existing = get_active_override_for_customer_date(customer_code, orig_date)
        if existing:
            existing.override_delivery_date = new_date
            existing.reason_code = reason_code
            existing.reason_notes = reason_notes
            existing.created_at = now
            existing.created_by = created_by
            updated += 1
        else:
            rec = CustomerDeliveryDateOverride(
                customer_code_365=customer_code,
                original_delivery_date=orig_date,
                override_delivery_date=new_date,
                reason_code=reason_code,
                reason_notes=reason_notes,
                is_active=True,
                created_at=now,
                created_by=created_by,
            )
            db.session.add(rec)
            created += 1

    db.session.commit()
    logger.info("Delivery overrides applied: %d created, %d updated, %d errors", created, updated, len(errors))
    return {"created": created, "updated": updated, "errors": errors}


def clear_delivery_overrides(clear_data: list[dict], cleared_by: str | None = None) -> dict:
    cleared = 0
    not_found = 0
    now = datetime.now(timezone.utc)

    for item in clear_data:
        customer_code = item.get("customer_code_365")
        try:
            orig_date = date.fromisoformat(item["original_delivery_date"])
        except (ValueError, KeyError):
            not_found += 1
            continue

        existing = get_active_override_for_customer_date(customer_code, orig_date)
        if existing:
            existing.is_active = False
            existing.cleared_at = now
            existing.cleared_by = cleared_by
            cleared += 1
        else:
            not_found += 1

    db.session.commit()
    logger.info("Delivery overrides cleared: %d cleared, %d not found", cleared, not_found)
    return {"cleared": cleared, "not_found": not_found}
