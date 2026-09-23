"""One bounded calculation, launched only by the server with a private job file."""
import json
from pathlib import Path
import sys


def main() -> int:
    root = Path(sys.argv[1]).resolve()
    if sys.platform == 'linux':
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
        resource.setrlimit(resource.RLIMIT_CPU, (115, 115))
        resource.setrlimit(resource.RLIMIT_FSIZE, (150 * 1024**2, 150 * 1024**2))
    from src.config import Config
    from src.pipeline import run_pipeline
    from src.security import SecurityError
    try:
        request = json.loads((root / 'request.json').read_text(encoding='utf-8'))
        paths = request['paths']
        run_pipeline(paths['sales'], Config(review_period_days=request['review'], service_level_z=request['z'],
                                            output_dir=root / 'outputs', processed_dir=root / 'processed',
                                            model_dir=root / 'models', persist_model=False,
                                            write_transaction_audit=False),
                     suppliers_path=paths['suppliers'], catalog_path=paths['catalog'], stock_path=paths['stock'],
                     stockouts_path=paths['stockouts'], growth_path=paths['growth'],
                     warehouse=request['warehouse'], category=request['category'])
        return 0
    except Exception as exc:
        safe = {'code': exc.code, 'message': str(exc)} if isinstance(exc, SecurityError) else {
            'code': 'invalid_dataset', 'message': 'Проверьте обязательные колонки, числа, даты и параметры расчёта.'}
        (root / 'error.json').write_text(json.dumps(safe), encoding='utf-8')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
