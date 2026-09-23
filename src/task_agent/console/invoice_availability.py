"""Application availability is separate from expiring per-run authority."""

from datetime import UTC, datetime


def validate_availability(mode, expires_at):
    if mode == 'operator-managed':
        if expires_at is not None:
            raise ValueError('operator-managed availability has no service deadline')
    elif mode != 'leased' or expires_at is None or expires_at.tzinfo is None:
        raise ValueError('explicit availability mode and aware leased deadline required')


def configured_expiry(mode, value):
    expiry = datetime.fromisoformat(value.replace('Z', '+00:00')) if value else None
    validate_availability(mode, expiry)
    if expiry is not None and not 0 < (expiry-datetime.now(UTC)).total_seconds() <= 7200:
        raise ValueError('bounded invoice service lease required')
    return expiry