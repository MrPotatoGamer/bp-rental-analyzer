from __future__ import annotations

import datetime as dt
import logging
import re
from pathlib import Path
from typing import Sequence

import pandas as pd

from .scraper import RawListing

logger = logging.getLogger(__name__)

_DISTRICT_RE = re.compile(r"Budapest\s+(?P<roman>[IVXLCDM]+)\.?\s*kerület", re.IGNORECASE)


def clean_price_huf(price_text: str | None) -> int | None:
    """Parse HUF price from strings like '350 000 Ft/hó'."""

    if not price_text:
        return None

    # Only handle HUF for now.
    if "Ft" not in price_text:
        return None

    groups = re.findall(r"\d+", price_text)
    if not groups:
        return None

    # ingatlan.com card text often contains a leading small number (e.g. image count)
    # right before the actual price: "9 290 000 Ft/hó" -> 290000
    # We generate candidates from the last N groups and choose a plausible one.
    #
    # Important: avoid candidates that would start with a leading-zero thousands block
    # (e.g. "070 070"), because that usually means we accidentally dropped the real
    # million/hundred-thousand part (e.g. "1 070 070").
    candidates: list[tuple[int, list[str]]] = []
    for k in range(2, min(5, len(groups) + 1)):
        try:
            used_groups = list(groups[-k:])
            candidates.append((int("".join(used_groups)), used_groups))
        except ValueError:
            continue

    if candidates:
        plausible = [
            value
            for (value, used) in candidates
            if 50_000 <= value <= 5_000_000
            and not (len(used[0]) == 3 and used[0].startswith("0"))
        ]
        if plausible:
            return plausible[0]

    try:
        return int("".join(groups))
    except ValueError:
        return None


def clean_area_m2(area_text: str | None) -> float | None:
    """Parse area in m² from strings like '46 m2'."""

    if not area_text:
        return None

    m = re.search(r"(\d+(?:[\.,]\d+)?)", area_text)
    if not m:
        return None

    value = m.group(1).replace(",", ".")
    try:
        return float(value)
    except ValueError:
        return None


def roman_to_int(roman: str) -> int | None:
    roman = roman.upper().strip().rstrip(".")
    if not roman:
        return None

    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    if any(ch not in values for ch in roman):
        return None

    total = 0
    prev = 0
    for ch in reversed(roman):
        val = values[ch]
        if val < prev:
            total -= val
        else:
            total += val
            prev = val

    return total


def parse_district(location_text: str | None) -> int | None:
    """Extract district number from 'Budapest XIII. kerület, ...'."""

    if not location_text:
        return None

    m = _DISTRICT_RE.search(location_text)
    if not m:
        return None

    return roman_to_int(m.group("roman"))


def to_dataframe(listings: Sequence[RawListing]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for item in listings:
        rows.append(
            {
                "listing_id": item.listing_id,
                "url": item.url,
                "title": item.title,
                "location_text": item.location_text,
                "price_text": item.price_text,
                "area_text": item.area_text,
                "rooms_text": item.rooms_text,
                "raw_text": item.raw_text,
                "source_url": item.source_url,
                "scraped_at": item.scraped_at.isoformat(),
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["price_huf"] = df["price_text"].map(clean_price_huf)
    df["area_m2"] = df["area_text"].map(clean_area_m2)
    df["district"] = df["location_text"].map(parse_district)

    df["price_per_sqm_huf"] = df["price_huf"] / df["area_m2"]
    df.loc[df["price_huf"].isna() | df["area_m2"].isna() | (df["area_m2"] <= 0), "price_per_sqm_huf"] = None

    return df


def save_snapshot(df: pd.DataFrame, *, data_dir: str = "data", date: dt.date | None = None) -> Path:
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    # Keep snapshots per-run (timestamped) so trends can show changes over time.
    # Backward compatible: if `date` is provided, it still influences the day-part.
    now = dt.datetime.now()
    snapshot_date = date or now.date()
    snapshot_ts = dt.datetime.combine(snapshot_date, now.time()).replace(microsecond=0)
    out_path = Path(data_dir) / f"listings_{snapshot_ts:%Y-%m-%d_%H%M%S}.csv"
    df.to_csv(out_path, index=False, encoding="utf-8")
    logger.info("Saved snapshot: %s", out_path)
    return out_path


def load_snapshots(*, data_dir: str = "data", days: int = 7) -> list[pd.DataFrame]:
    p = Path(data_dir)
    if not p.exists():
        return []

    today = dt.date.today()
    min_date = today - dt.timedelta(days=max(1, days) - 1)

    snapshots: list[pd.DataFrame] = []
    for file in sorted(p.glob("listings_*.csv")):
        snapshot_ts: dt.datetime | None = None
        d: dt.date | None = None

        # New format: listings_YYYY-MM-DD_HHMMSS.csv
        m_ts = re.match(r"listings_(\d{4}-\d{2}-\d{2})_(\d{6})\.csv$", file.name)
        if m_ts:
            try:
                snapshot_ts = dt.datetime.strptime(f"{m_ts.group(1)} {m_ts.group(2)}", "%Y-%m-%d %H%M%S")
                d = snapshot_ts.date()
            except ValueError:
                continue
        else:
            # Old format: listings_YYYY-MM-DD.csv
            m_date = re.match(r"listings_(\d{4}-\d{2}-\d{2})\.csv$", file.name)
            if not m_date:
                continue
            try:
                d = dt.date.fromisoformat(m_date.group(1))
                snapshot_ts = dt.datetime.combine(d, dt.time.min)
            except ValueError:
                continue

        if d is None or d < min_date:
            continue

        df = pd.read_csv(file)
        df["snapshot_date"] = d.isoformat()
        df["snapshot_ts"] = snapshot_ts.isoformat() if snapshot_ts else d.isoformat()
        snapshots.append(df)

    return snapshots


def compute_weekly_trend(snapshots: Sequence[pd.DataFrame]) -> pd.DataFrame:
    """Return a daily time series of avg price_per_sqm_huf for the last N snapshots."""

    rows: list[dict[str, object]] = []
    for df in snapshots:
        if df.empty or "snapshot_date" not in df.columns:
            continue

        # Prefer timestamped snapshots for higher fidelity; fall back to date.
        date_str = str(df["snapshot_ts"].iloc[0]) if "snapshot_ts" in df.columns else str(df["snapshot_date"].iloc[0])
        series = df.get("price_per_sqm_huf")
        values = pd.to_numeric(series, errors="coerce").dropna() if series is not None else pd.Series(dtype=float)

        rows.append(
            {
                "date": date_str,
                "avg_price_per_sqm_huf": float(values.mean()) if not values.empty else None,
                "listing_count": int(len(df)),
            }
        )

    trend = pd.DataFrame(rows)
    if trend.empty:
        return trend

    trend = trend.sort_values("date")
    return trend


def find_deals(
    df: pd.DataFrame,
    *,
    district: int | None = None,
    min_area: float | None = None,
    max_area: float | None = None,
    threshold_pct: float = 0.15,
) -> tuple[pd.DataFrame, float | None]:
    """Find listings that are `threshold_pct` below the segment average (price per m²).

    Returns: (deals_df, segment_avg_price_per_sqm)
    """

    if df.empty:
        return df.copy(), None

    segment = df.copy()

    if district is not None:
        segment = segment[segment["district"] == district]

    if min_area is not None:
        segment = segment[pd.to_numeric(segment["area_m2"], errors="coerce") >= min_area]

    if max_area is not None:
        segment = segment[pd.to_numeric(segment["area_m2"], errors="coerce") <= max_area]

    metric = pd.to_numeric(segment.get("price_per_sqm_huf"), errors="coerce").dropna()
    if metric.empty:
        return segment.iloc[0:0].copy(), None

    avg = float(metric.mean())
    threshold_value = avg * (1.0 - float(threshold_pct))

    deals = segment.copy()
    deals["price_per_sqm_huf"] = pd.to_numeric(deals["price_per_sqm_huf"], errors="coerce")
    deals = deals[deals["price_per_sqm_huf"].notna()]
    deals = deals[deals["price_per_sqm_huf"] <= threshold_value]

    deals["deal_delta_pct"] = deals["price_per_sqm_huf"].apply(lambda v: (v / avg) - 1.0)
    deals = deals.sort_values("price_per_sqm_huf", ascending=True)

    return deals, avg


def basic_summary(df: pd.DataFrame) -> dict[str, object]:
    if df.empty:
        return {
            "listing_count": 0,
            "avg_price_huf": None,
            "avg_price_per_sqm_huf": None,
        }

    price = pd.to_numeric(df.get("price_huf"), errors="coerce").dropna()
    pps = pd.to_numeric(df.get("price_per_sqm_huf"), errors="coerce").dropna()

    return {
        "listing_count": int(len(df)),
        "avg_price_huf": float(price.mean()) if not price.empty else None,
        "avg_price_per_sqm_huf": float(pps.mean()) if not pps.empty else None,
    }
