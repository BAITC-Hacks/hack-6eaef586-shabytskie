"""Shared limits and redacted security events (no user payloads in logs)."""
import hashlib
import logging
import math
import re
import uuid

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_BATCH_BYTES = 60 * 1024 * 1024
MAX_ROWS = 100_000
MAX_COLUMNS = 40
MAX_CELLS = 2_000_000
MAX_CELL_CHARS = 2048
MAX_SKUS = 2000
MAX_WAREHOUSES = 100
MAX_DAYS = 3653
MAX_DAILY_ROWS = 200_000
MAX_FORECAST_DAYS = 365
MAX_QUANTITY = 1_000_000_000


class SecurityError(ValueError):
    """An intentional, safe-to-display rejection with HTTP-like semantics."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


def security_event(event: str, actor: str = '', code: str = '', reference: str = '') -> None:
    """Only controlled labels and a hashed subject may enter the audit log."""
    label = lambda value: re.sub(r'[^a-zA-Z0-9_.-]', '_', value)[:64]
    subject = hashlib.sha256(actor.encode()).hexdigest()[:16] if actor else '-'
    logging.getLogger('security').info('event=%s actor=%s code=%s reference=%s',
                                     label(event), subject, label(code), label(reference))


def public_error(exc: Exception, actor: str = '') -> str:
    if isinstance(exc, SecurityError):
        security_event('request_rejected', actor, exc.code)
        return str(exc)
    reference = uuid.uuid4().hex[:12]
    # Avoid exception text: parser errors can contain rows, secrets and paths.
    security_event('request_failed', actor, type(exc).__name__, reference)
    return f'Не удалось обработать запрос. Проверьте данные. Код ошибки: {reference}.'


def bounded_number(value, field: str, minimum: float, maximum: float, *, integer=False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float('nan')
    if isinstance(value, bool) or not math.isfinite(number) or not minimum <= number <= maximum:
        raise SecurityError('numeric_bounds', f'{field}: допустимый диапазон {minimum:g}–{maximum:g}.')
    if integer and not number.is_integer():
        raise SecurityError('integer_required', f'{field}: требуется целое число.')
    return number


def bounded_text(value: str | None, field: str, maximum: int = 128) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise SecurityError('text_bounds', f'{field}: недопустимая длина или управляющие символы.')
    return value.strip() or None
