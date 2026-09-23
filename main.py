import argparse
import logging
from pathlib import Path
from src.config import Config
from src.pipeline import run_pipeline


DEMO = Path('data/raw')


def use_demo_data(args: argparse.Namespace) -> None:
    args.input = DEMO / 'synthetic_sales.csv'
    if not args.input.exists():
        from src.generate_synthetic_data import generate
        logging.info('Generating demo data in %s', DEMO)
        generate(args.input)
    for name, file in [('suppliers', 'suppliers.csv'), ('catalog', 'catalog.csv'),
                       ('stockouts', 'stockouts.csv'), ('growth', 'category_growth.csv')]:
        if getattr(args, name) is None and (DEMO / file).exists():
            setattr(args, name, DEMO / file)
    logging.info('No --input given: using demo data %s', args.input)


def main() -> int:
    parser = argparse.ArgumentParser(description='Рекомендованные заказы поставщикам на основе прогноза спроса')
    parser.add_argument('--input', type=Path, help='история продаж (CSV/XLSX); без него — демо-данные из data/raw')
    parser.add_argument('--suppliers', type=Path, help='справочник поставщиков: supplier, lead_time_days[, min_order_qty]')
    parser.add_argument('--catalog', type=Path, help='номенклатура: sku[, product_name, category, supplier, min_order_qty, order_multiple]')
    parser.add_argument('--stock', type=Path, help='текущие остатки: sku, stock[, in_transit, warehouse]')
    parser.add_argument('--stockouts', type=Path, help='периоды отсутствия: sku, date_from[, date_to]')
    parser.add_argument('--growth', type=Path, help='прогноз прироста: category или sku, growth_forecast (0.1 или 10%%)')
    parser.add_argument('--warehouse', help='рассчитать только по одному складу')
    parser.add_argument('--category', help='рассчитать только по одной категории')
    parser.add_argument('--forecast-days', type=int, default=30)
    parser.add_argument('--review-days', type=int, default=7, help='период пересмотра заказа, дней')
    parser.add_argument('--service-z', type=float, default=1.65, help='z уровня сервиса для страхового запаса')
    parser.add_argument('--default-lead-time', type=int, default=14)
    parser.add_argument('--chart-sku')
    parser.add_argument('--cv-splits', type=int, default=0, help='0: 80/20 holdout; >=2: expanding date folds')
    parser.add_argument('--output-dir', type=Path, default=Path('outputs'))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
    if args.input is None:
        use_demo_data(args)
    try:
        if args.cv_splits == 1 or args.cv_splits < 0:
            raise ValueError('--cv-splits must be 0 or >=2')
        config = Config(forecast_days=args.forecast_days, output_dir=args.output_dir,
                        review_period_days=args.review_days, service_level_z=args.service_z,
                        default_lead_time_days=args.default_lead_time)
        run_pipeline(args.input, config, args.chart_sku, args.cv_splits, args.suppliers, args.catalog,
                     args.stock, args.stockouts, args.growth, args.warehouse, args.category)
    except (ValueError, OSError, ImportError) as exc:
        logging.error('%s', exc)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
