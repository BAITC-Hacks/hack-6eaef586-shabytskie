"""OIDC identity adaptation and server-side action authorization."""
from dataclasses import dataclass
import math
import os
import time
from urllib.parse import urlsplit

from src.security import SecurityError, security_event, public_error

PERMISSIONS = {
    'demo': {'demo', 'read', 'export', 'approve_demo'},
    'viewer': {'demo', 'read', 'export'},
    'analyst': {'demo', 'read', 'export', 'upload', 'calculate'},
    'manager': {'demo', 'read', 'export', 'upload', 'calculate', 'approve'},
    'admin': {'demo', 'read', 'export', 'upload', 'calculate', 'approve'},
}


@dataclass(frozen=True)
class Principal:
    subject: str
    role: str
    expires_at: float


def principal_from_claims(claims: dict, *, logged_in: bool, issuer: str,
                          roles: dict, now: float | None = None) -> Principal:
    """Only pass claims verified by Streamlit/Authlib, never request JSON/JWTs."""
    now = time.time() if now is None else now
    if not logged_in:
        raise SecurityError('login_required', 'Требуется вход.', 401)
    subject = claims.get('sub')
    try:
        expires = float(claims.get('exp', 0))
    except (TypeError, ValueError):
        expires = 0
    if (not isinstance(subject, str) or not 1 <= len(subject) <= 256
            or not issuer or claims.get('iss') != issuer or not math.isfinite(expires) or expires <= now):
        raise SecurityError('identity_invalid', 'Сессия недействительна или истекла. Войдите снова.', 401)
    role = roles.get(subject)
    if role not in PERMISSIONS or role == 'demo':
        raise SecurityError('user_not_allowed', 'Доступ не предоставлен администратором.', 403)
    return Principal(f'{issuer}|{subject}', role, expires)


def authorize(principal: Principal | None, action: str) -> None:
    if principal is None or principal.expires_at <= time.time():
        raise SecurityError('login_required', 'Требуется действующая сессия.', 401)
    if action not in PERMISSIONS.get(principal.role, set()):
        security_event('access_denied', principal.subject, action)
        raise SecurityError('access_denied', 'Недостаточно прав для этого действия.', 403)


def _check_oidc_config(auth: dict, access: dict) -> None:
    for field in ['redirect_uri', 'server_metadata_url']:
        parsed = urlsplit(str(auth.get(field, '')))
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise SecurityError('auth_configuration', 'Администратор должен настроить HTTPS и OIDC.', 503)
    if (len(str(auth.get('cookie_secret', ''))) < 32 or not auth.get('client_id')
            or not auth.get('client_secret') or auth.get('expose_tokens')
            or not access.get('issuer') or not isinstance(access.get('users'), dict)):
        raise SecurityError('auth_configuration', 'Администратор должен настроить OIDC и список доступа.', 503)


def require_principal() -> Principal:
    """Fail closed in production; public demo never exposes real-file inputs."""
    import streamlit as st
    mode = os.environ.get('APP_MODE', 'demo')
    if mode == 'demo':
        st.caption('Демонстрация на синтетических данных. Реальные файлы доступны после настройки входа.')
        return Principal('public-demo', 'demo', float('inf'))
    if mode != 'production':
        st.error('Неизвестный режим приложения. Обратитесь к администратору.')
        st.stop()
    try:
        auth, access = st.secrets['auth'].to_dict(), st.secrets['access'].to_dict()
        _check_oidc_config(auth, access)
    except Exception as exc:
        st.error(public_error(exc))
        st.stop()
    if not st.user.is_logged_in:
        st.title('Вход в систему закупок')
        if st.button('Войти через корпоративную учётную запись', key='login'):
            try:
                security_event('login_started')
                st.login()
            except Exception as exc:
                st.error(public_error(exc))
        st.stop()
    try:
        principal = principal_from_claims(st.user.to_dict(), logged_in=True,
                                           issuer=access['issuer'], roles=access['users'])
    except SecurityError as exc:
        st.error(public_error(exc))
        if st.button('Выйти и войти снова', key='login_again'):
            st.logout()
        st.stop()
    with st.sidebar:
        st.text(f'Роль: {principal.role}')
    return principal
