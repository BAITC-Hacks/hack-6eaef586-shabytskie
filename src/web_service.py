"""Session-owned workspaces and bounded operations behind the Streamlit UI."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
# Fixed interpreter/worker with no shell and a bounded runtime.
import subprocess  # nosec B404
import sys
import tempfile
import threading
import time
import uuid

import pandas as pd

from src.config import Config
from src.ordering import EXPORT_COLUMNS, STATUS_MISSING_STOCK, to_1c_csv, validate_approved_orders
from src.secure_files import checked_suffix, validate_size, to_excel_bytes
from src.security import (MAX_FILE_BYTES, MAX_BATCH_BYTES, SecurityError, bounded_text, security_event)
from src.web_auth import Principal, authorize

PROJECT = Path(__file__).resolve().parents[1]
INPUT_NAMES = {'sales', 'suppliers', 'catalog', 'stock', 'stockouts', 'growth'}
DEMO_FILES = {'sales': 'synthetic_sales.csv', 'suppliers': 'suppliers.csv', 'catalog': 'catalog.csv',
              'stock': None, 'stockouts': 'stockouts.csv', 'growth': 'category_growth.csv'}
ARTIFACTS = {'orders': 'outputs/supplier_orders.csv', 'daily': 'processed/daily_demand.csv',
             'future': 'outputs/daily_forecast.csv', 'xlsx': 'outputs/supplier_orders.xlsx'}


class JobLimiter:
    """Single-process limits; production ingress supplies cross-process/IP limits."""
    def __init__(self, per_user=3, total=12, window=300):
        self.per_user, self.total, self.window = per_user, total, window
        self._events = []
        self._lock = threading.Lock()
        self._running = False

    @contextmanager
    def hold(self, actor: str):
        with self._lock:
            now = time.monotonic()
            self._events = [(t, user) for t, user in self._events if now - t < self.window]
            if (self._running or len(self._events) >= self.total
                    or sum(user == actor for _, user in self._events) >= self.per_user):
                security_event('rate_limited', actor)
                raise SecurityError('rate_limit', 'Лимит расчётов достигнут или сервер занят. Повторите позже.', 429)
            self._events.append((now, actor))
            self._running = True
        try:
            yield
        finally:
            with self._lock:
                self._running = False


@dataclass
class Workspace:
    owner: str
    session: str
    temporary: tempfile.TemporaryDirectory
    expires_at: float
    demo: bool
    ready: bool = False

    @property
    def root(self) -> Path:
        return Path(self.temporary.name)


class WebService:
    """Tokens resolve through server-owned records, never through user paths."""
    def __init__(self, ttl=3600, maximum=24, reap=False):
        self.ttl, self.maximum = ttl, maximum
        self.workspaces: dict[str, Workspace] = {}
        self.limiter = JobLimiter()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        if reap:
            threading.Thread(target=self._reap_loop, daemon=True, name='workspace-cleanup').start()

    def _reap_loop(self):
        while not self._stop.wait(60):
            self.prune()

    def prune(self):
        with self._lock:
            for token, item in list(self.workspaces.items()):
                if item.expires_at <= time.time():
                    item.temporary.cleanup()
                    del self.workspaces[token]

    def close(self):
        self._stop.set()
        with self._lock:
            for item in self.workspaces.values():
                item.temporary.cleanup()
            self.workspaces.clear()

    def create(self, principal: Principal, session: str, *, demo: bool) -> str:
        authorize(principal, 'demo' if demo else 'calculate')
        self.prune()
        with self._lock:
            if len(self.workspaces) >= self.maximum:
                raise SecurityError('workspace_limit', 'Сервер занят. Повторите позже.', 429)
            token = secrets.token_urlsafe(32)
            self.workspaces[token] = Workspace(principal.subject, session,
                                               tempfile.TemporaryDirectory(prefix='procurement_'),
                                               time.time() + self.ttl, demo)
            return token

    def resolve(self, token: str, principal: Principal, session: str) -> Workspace:
        authorize(principal, 'read')
        with self._lock:
            item = self.workspaces.get(token)
            if item is None or item.owner != principal.subject or item.session != session:
                security_event('access_denied', principal.subject, 'workspace_owner')
                raise SecurityError('workspace_owner', 'Результат недоступен.', 403)
            if item.expires_at <= time.time():
                raise SecurityError('workspace_expired', 'Срок хранения результата истёк. Повторите расчёт.', 410)
            return item

    def discard(self, token: str, principal: Principal, session: str):
        item = self.resolve(token, principal, session)
        with self._lock:
            item.temporary.cleanup()
            self.workspaces.pop(token, None)

    def _save_uploads(self, uploads: dict, root: Path) -> dict:
        if set(uploads) - INPUT_NAMES or uploads.get('sales') is None:
            raise SecurityError('upload_fields', 'Нужна история продаж и только разрешённые типы справочников.')
        if sum(upload.size for upload in uploads.values() if upload is not None) > MAX_BATCH_BYTES:
            raise SecurityError('batch_size', 'Суммарный размер файлов превышает 60 МБ.', 413)
        paths = {}
        actual_bytes = 0
        for key in sorted(INPUT_NAMES):
            upload = uploads.get(key)
            if upload is None:
                paths[key] = None
                continue
            validate_size(upload.size)
            suffix = checked_suffix(upload.name, getattr(upload, 'type', None))
            # Read at most the cap, even if a forged size property claims less.
            upload.seek(0)
            data = upload.read(MAX_FILE_BYTES + 1)
            validate_size(len(data))
            actual_bytes += len(data)
            if actual_bytes > MAX_BATCH_BYTES:
                raise SecurityError('batch_size', 'Суммарный размер файлов превышает 60 МБ.', 413)
            path = root / f'{uuid.uuid4().hex}{suffix}'
            with path.open('xb') as handle:
                handle.write(data)
            paths[key] = str(path)
        return paths

    def calculate(self, principal: Principal, session: str, *, demo: bool, uploads: dict | None = None,
                  review=7, z=1.65, warehouse=None, category=None) -> str:
        authorize(principal, 'demo' if demo else 'calculate')
        if not demo:
            authorize(principal, 'upload')
        config = Config(review_period_days=review, service_level_z=z)
        warehouse = bounded_text(warehouse, 'warehouse')
        category = bounded_text(category, 'category')
        with self.limiter.hold(principal.subject):
            token = self.create(principal, session, demo=demo)
            item = self.resolve(token, principal, session)
            try:
                paths = ({key: str(PROJECT / 'data/demo' / filename) if filename else None
                          for key, filename in DEMO_FILES.items()} if demo else self._save_uploads(uploads or {}, item.root))
                request = {'paths': paths, 'review': config.review_period_days, 'z': config.service_level_z,
                           'warehouse': warehouse, 'category': category}
                (item.root / 'request.json').write_text(json.dumps(request), encoding='utf-8')
                security_event('calculation_started', principal.subject)
                self._run_worker(item.root)
                item.ready = True
                security_event('calculation_completed', principal.subject)
                return token
            except Exception:
                item.temporary.cleanup()
                with self._lock:
                    self.workspaces.pop(token, None)
                raise
            finally:
                # Raw input and job instructions are no longer needed.
                for path in item.root.glob('*'):
                    if path.is_file():
                        path.unlink()

    def _run_worker(self, root: Path):
        env = {key: value for key, value in os.environ.items()
               if key in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'LANG', 'LC_ALL'}}
        env.update(OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1',
                   NUMEXPR_NUM_THREADS='1', MPLCONFIGDIR=str(root / 'mpl'))
        try:
            # Fixed executable/module, server-generated directory, no shell or user command.
            completed = subprocess.run(  # nosec B603
                [sys.executable, '-m', 'src.worker', str(root)], cwd=PROJECT, env=env,
                shell=False, timeout=120, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.TimeoutExpired as exc:
            raise SecurityError('job_timeout', 'Расчёт превысил 120 секунд. Уменьшите объём данных.', 429) from exc
        if completed.returncode:
            error_file = root / 'error.json'
            if error_file.is_file():
                error = json.loads(error_file.read_text(encoding='utf-8'))
                raise SecurityError(error['code'], error['message'])
            raise SecurityError('job_failed', 'Расчёт не выполнен. Проверьте формат и объём данных.')

    def artifact(self, token: str, principal: Principal, session: str, name: str) -> Path:
        item = self.resolve(token, principal, session)
        if name not in ARTIFACTS or not item.ready:
            raise SecurityError('artifact', 'Результат недоступен.', 403)
        path = item.root / ARTIFACTS[name]
        if path.is_symlink() or not path.resolve().is_relative_to(item.root.resolve()):
            raise SecurityError('artifact_path', 'Результат недоступен.', 403)
        return path

    def load_results(self, token: str, principal: Principal, session: str) -> tuple:
        return tuple(pd.read_csv(self.artifact(token, principal, session, name), dtype={'sku': str},
                                 **({'parse_dates': ['date']} if name != 'orders' else {}))
                     for name in ['orders', 'daily', 'future'])

    def download(self, token: str, principal: Principal, session: str) -> bytes:
        authorize(principal, 'export')
        return self.artifact(token, principal, session, 'xlsx').read_bytes()

    def approve(self, token: str, principal: Principal, session: str, supplier: str,
                edits: pd.DataFrame) -> tuple[str, bytes, bytes]:
        item = self.resolve(token, principal, session)
        authorize(principal, 'approve_demo' if principal.role == 'demo' and item.demo else 'approve')
        orders = pd.read_csv(self.artifact(token, principal, session, 'orders'), dtype={'sku': str})
        approved = apply_approval_edits(orders, supplier, edits)
        approved_at = datetime.now(timezone.utc)
        actor = 'DEMO — учебное утверждение' if item.demo and principal.role == 'demo' else principal.subject
        approved['status'] = f'утверждён: {actor} {approved_at:%Y-%m-%d %H:%M:%S UTC}'
        approved['order_value'] = approved.approved_qty * approved.price
        table = approved[list(EXPORT_COLUMNS)].rename(columns=EXPORT_COLUMNS)
        stem = f'order_{approved_at:%Y%m%d_%H%M%S}_{uuid.uuid4().hex}'
        security_event('order_approved', principal.subject, reference=stem)
        return stem, to_excel_bytes(table), to_1c_csv(table)


def apply_approval_edits(orders: pd.DataFrame, supplier: str, edits: pd.DataFrame) -> pd.DataFrame:
    """Rebuild from server-owned rows: the browser may only change quantity."""
    if set(edits) != {'sku', 'approved_qty'} or edits.sku.duplicated().any() or edits.sku.isna().any():
        raise SecurityError('approval_fields', 'Недопустимые поля или дубли в изменениях заказа.')
    selected = orders[orders.supplier == supplier].set_index('sku')
    if not len(edits) or not set(edits.sku) <= set(selected.index):
        raise SecurityError('approval_owner', 'Артикул не принадлежит выбранному поставщику.', 403)
    approved = selected.loc[edits.sku].reset_index()
    approved['approved_qty'] = edits.approved_qty.to_numpy()
    untouched = approved.status.eq(STATUS_MISSING_STOCK) & approved.approved_qty.isna()
    approved.loc[untouched, 'approved_qty'] = 0.
    approved = validate_approved_orders(approved)
    approved = approved[approved.approved_qty > 0]
    if approved.empty:
        raise SecurityError('approval_empty', 'Нет позиций с количеством больше нуля.')
    return approved
