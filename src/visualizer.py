from __future__ import annotations

import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

logger = logging.getLogger(__name__)


def plot_district_ranking(
    df: pd.DataFrame,
    *,
    out_path: str,
    metric_col: str = "price_per_sqm_huf",
) -> Path | None:
    if df.empty or metric_col not in df.columns or "district" not in df.columns:
        logger.warning("No data to plot district ranking")
        return None

    data = df[["district", metric_col]].copy()
    data["district"] = pd.to_numeric(data["district"], errors="coerce")
    data[metric_col] = pd.to_numeric(data[metric_col], errors="coerce")
    data = data.dropna()

    if data.empty:
        logger.warning("No numeric district/metric values to plot")
        return None

    ranking = data.groupby("district", as_index=False)[metric_col].mean().sort_values(metric_col)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    sns.set_theme(style="whitegrid")
    plt.figure(figsize=(12, 6))
    ax = sns.barplot(data=ranking, x="district", y=metric_col)
    ax.set_xlabel("Budapest kerület")
    ax.set_ylabel("Átlag ár / m² (Ft/hó/m²)")
    ax.set_title("Kerületi rangsor (ár / m²)")

    plt.tight_layout()
    plt.savefig(out, dpi=160)
    plt.close()

    logger.info("Saved chart: %s", out)
    return out


def plot_weekly_trend(
    trend_df: pd.DataFrame,
    *,
    out_path: str,
    metric_col: str = "avg_price_per_sqm_huf",
) -> Path | None:
    if trend_df.empty or metric_col not in trend_df.columns or "date" not in trend_df.columns:
        logger.warning("No data to plot weekly trend")
        return None

    data = trend_df.copy()
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    data[metric_col] = pd.to_numeric(data[metric_col], errors="coerce")
    data = data.dropna(subset=["date", metric_col])

    if data.empty:
        logger.warning("No valid weekly trend points")
        return None

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    sns.set_theme(style="whitegrid")
    plt.figure(figsize=(12, 5))
    ax = sns.lineplot(data=data.sort_values("date"), x="date", y=metric_col, marker="o")
    ax.set_xlabel("Dátum")
    ax.set_ylabel("Átlag ár / m² (Ft/hó/m²)")
    ax.set_title("Heti trend (ár / m²)")

    plt.tight_layout()
    plt.savefig(out, dpi=160)
    plt.close()

    logger.info("Saved chart: %s", out)
    return out
