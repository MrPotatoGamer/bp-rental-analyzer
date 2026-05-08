from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import logging
import re
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

from src.processor import basic_summary, compute_weekly_trend, find_deals, load_snapshots, save_snapshot, to_dataframe
from src.reporter import generate_pdf_report
from src.scraper import IngatlanScraper
from src.visualizer import plot_district_ranking, plot_weekly_trend

DEFAULT_URL = "https://ingatlan.com/budapest/kiado+lakas"

logger = logging.getLogger(__name__)


def make_search_key(url: str) -> str:
    """Create a filesystem-safe key for a search URL.

    Used to keep snapshots/trends separated per query so results don't mix.
    """

    parsed = urlparse(url)
    base = (parsed.path or "search").strip("/")
    base = base.replace("+", "_").replace("/", "_")
    base = re.sub(r"[^A-Za-z0-9_\-]+", "_", base).strip("_")
    if not base:
        base = "search"
    base = base[:40]

    short_hash = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    return f"{base}_{short_hash}"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Budapest rental analyzer: scrape ingatlan.com, compute price/m², find deals, generate charts and PDF.",
    )

    p.add_argument("--url", default=DEFAULT_URL, help="ingatlan.com keresési URL")
    p.add_argument("--pages", type=int, default=1, help="Hány találati oldalt kérjünk le")

    p.add_argument("--district", type=int, default=None, help="Deal-finder szűrés: kerület (pl. 13)")
    p.add_argument("--min-area", type=float, default=None, help="Deal-finder szűrés: minimum alapterület (m²)")
    p.add_argument("--max-area", type=float, default=None, help="Deal-finder szűrés: maximum alapterület (m²)")
    p.add_argument(
        "--deal-threshold",
        type=float,
        default=0.15,
        help="Deal küszöb: ennyivel legyen az átlag alatt (pl. 0.15 = -15%%)",
    )
    p.add_argument("--top-deals", type=int, default=5, help="Hány deal kerüljön a PDF-be")

    p.add_argument("--data-dir", default="data", help="CSV snapshot mappa")
    p.add_argument("--images-dir", default="images", help="Grafikonok mappája")

    p.add_argument(
        "--logo-path",
        default="images/company_logo.png",
        help="PDF fejléc logó (PNG/JPG). Ha nem létezik, kihagyjuk.",
    )
    p.add_argument(
        "--pdf-path",
        default=None,
        help="PDF kimeneti útvonal (alap: report_YYYY-MM-DD.pdf a projekt gyökerében)",
    )

    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging szint",
    )

    return p


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Keep 3rd party libs quiet unless debugging them.
    logging.getLogger("matplotlib").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)


def main() -> int:
    args = build_parser().parse_args()
    configure_logging(args.log_level)

    images_dir = Path(args.images_dir)
    data_dir = Path(args.data_dir)
    images_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    # Keep each search's history separate, otherwise trends become misleading.
    search_key = make_search_key(args.url)
    run_data_dir = data_dir / search_key
    run_data_dir.mkdir(parents=True, exist_ok=True)

    scraper = IngatlanScraper()
    raw_listings = scraper.fetch_listings(args.url, pages=max(1, args.pages), delay_s=1.0)

    df = to_dataframe(raw_listings)
    if df.empty:
        logger.error("Nem sikerült hirdetéseket kinyerni. (Lehet Cloudflare/HTML változás.)")
        return 2

    save_snapshot(df, data_dir=str(run_data_dir))

    # Charts
    district_chart = plot_district_ranking(
        df,
        out_path=str(images_dir / "district_ranking.png"),
    )

    snapshots = load_snapshots(data_dir=str(run_data_dir), days=7)
    trend_df = compute_weekly_trend(snapshots)
    weekly_chart = plot_weekly_trend(
        trend_df,
        out_path=str(images_dir / "weekly_trend.png"),
    )

    # Deal Finder
    deals_df, segment_avg = find_deals(
        df,
        district=args.district,
        min_area=args.min_area,
        max_area=args.max_area,
        threshold_pct=args.deal_threshold,
    )

    summary = basic_summary(df)
    summary["segment_avg_price_per_sqm_huf"] = segment_avg

    # Cheapest / most expensive district (current snapshot)
    district_metric = df[["district", "price_per_sqm_huf"]].copy()
    district_metric["district"] = pd.to_numeric(district_metric["district"], errors="coerce")
    district_metric["price_per_sqm_huf"] = pd.to_numeric(district_metric["price_per_sqm_huf"], errors="coerce")
    district_metric = district_metric.dropna(subset=["district", "price_per_sqm_huf"])

    if not district_metric.empty:
        by_district = district_metric.groupby("district")["price_per_sqm_huf"].mean()
        cheapest_d = float(by_district.idxmin())
        expensive_d = float(by_district.idxmax())
        summary["cheapest_district"] = int(cheapest_d)
        summary["cheapest_district_avg_pps"] = float(by_district.loc[cheapest_d])
        summary["most_expensive_district"] = int(expensive_d)
        summary["most_expensive_district_avg_pps"] = float(by_district.loc[expensive_d])

    # Default: generate a new PDF on every run (timestamped).
    # If you want overwriting behavior, pass an explicit --pdf-path.
    pdf_path = args.pdf_path or f"report_{dt.datetime.now():%Y-%m-%d_%H%M%S}.pdf"
    generate_pdf_report(
        output_path=pdf_path,
        title="Budapest Rental Analyzer – heti riport",
        search_url=args.url,
        summary=summary,
        deals_df=deals_df,
        district_chart_path=str(district_chart) if district_chart else None,
        weekly_chart_path=str(weekly_chart) if weekly_chart else None,
        logo_path=args.logo_path,
        generated_at=dt.datetime.now(),
        top_n=max(1, args.top_deals),
        threshold_pct=args.deal_threshold,
    )

    # Quick console recap (via logging)
    logger.info("Kész: %s hirdetés", summary.get("listing_count"))
    logger.info("Átlag fajlagos ár: %s Ft/hó/m²", summary.get("avg_price_per_sqm_huf"))
    if summary.get("cheapest_district") is not None:
        logger.info(
            "Legolcsóbb kerület (átlag): %s. ker (~%.0f Ft/hó/m²)",
            summary.get("cheapest_district"),
            float(summary.get("cheapest_district_avg_pps") or 0),
        )
    if summary.get("most_expensive_district") is not None:
        logger.info(
            "Legdrágább kerület (átlag): %s. ker (~%.0f Ft/hó/m²)",
            summary.get("most_expensive_district"),
            float(summary.get("most_expensive_district_avg_pps") or 0),
        )
    if segment_avg is not None:
        logger.info(
            "Deal baseline (%s. ker, %s-%s m²): %.0f Ft/hó/m²",
            args.district if args.district is not None else "*",
            args.min_area if args.min_area is not None else "*",
            args.max_area if args.max_area is not None else "*",
            segment_avg,
        )
        logger.info("Deal találatok: %s", len(deals_df))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
