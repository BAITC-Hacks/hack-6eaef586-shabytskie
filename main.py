"""Command-line entry point."""
import argparse
import logging
from pathlib import Path
from src.config import Config
from src.pipeline import run_pipeline


def main() -> int:
    parser = argparse.ArgumentParser(description='SKU demand forecasting (no procurement quantities)')
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--forecast-days', type=int, default=30)
    parser.add_argument('--chart-sku')
    parser.add_argument('--cv-splits', type=int, default=0, help='0: 80/20 holdout; >=2: expanding date folds')
    parser.add_argument('--output-dir', type=Path, default=Path('outputs'))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
    try:
        if args.cv_splits == 1 or args.cv_splits < 0:
            raise ValueError('--cv-splits must be 0 or >=2')
        run_pipeline(args.input, Config(forecast_days=args.forecast_days, output_dir=args.output_dir), args.chart_sku, args.cv_splits)
    except (ValueError, OSError, ImportError) as exc:
        logging.error('%s', exc)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
