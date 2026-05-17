from __future__ import annotations

import datetime as dt
import logging
import os
import unicodedata
from pathlib import Path

import pandas as pd
from fpdf import FPDF

logger = logging.getLogger(__name__)
logging.getLogger("fontTools").setLevel(logging.WARNING)
logging.getLogger("fontTools.subset").setLevel(logging.ERROR)


def _pdf_safe(value: object) -> str:
    if value is None:
        return ""

    text = str(value)
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", errors="ignore").decode("ascii")
    return text


def _pick_windows_ttf_fonts() -> dict[str, str]:
    fonts: dict[str, str] = {}

    env_regular = os.environ.get("RENTAL_ANALYZER_TTF")
    env_bold = os.environ.get("RENTAL_ANALYZER_TTF_BOLD")
    if env_regular and Path(env_regular).exists():
        fonts[""] = env_regular
    if env_bold and Path(env_bold).exists():
        fonts["B"] = env_bold
    if fonts.get(""):
        return fonts

    pairs = [
        ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf"),
        ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
        ("C:/Windows/Fonts/tahoma.ttf", "C:/Windows/Fonts/tahomabd.ttf"),
    ]
    for regular, bold in pairs:
        if Path(regular).exists():
            fonts[""] = regular
            if Path(bold).exists():
                fonts["B"] = bold
            return fonts

    return {}


def _fmt_int_hu(value: float | int | None) -> str:
    if value is None:
        return "-"
    try:
        return f"{int(round(float(value))):,}".replace(",", " ")
    except Exception:
        return "-"


def _fmt_float_hu(value: float | int | None, *, digits: int = 1) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.{digits}f}".replace(".", ",")
    except Exception:
        return "-"


def _wrap_long_text(text: str, *, max_len: int = 80) -> list[str]:
    if len(text) <= max_len:
        return [text]

    lines: list[str] = []
    start = 0
    while start < len(text):
        lines.append(text[start : start + max_len])
        start += max_len
    return lines


class RentalReportPDF(FPDF):
    def __init__(self, *, title: str, logo_path: str | None = None) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self._title = title
        self._logo_path = logo_path
        self.set_auto_page_break(auto=True, margin=15)

        self._font_family = "Helvetica"
        self._unicode_enabled = False
        self._try_enable_unicode()

    def _try_enable_unicode(self) -> None:
        fonts = _pick_windows_ttf_fonts()
        if not fonts.get(""):
            return
        try:
            self.add_font("Unicode", "", fonts[""], uni=True)
            if fonts.get("B"):
                self.add_font("Unicode", "B", fonts["B"], uni=True)
            self._font_family = "Unicode"
            self._unicode_enabled = True
        except TypeError:
            try:
                self.add_font("Unicode", "", fonts[""])
                if fonts.get("B"):
                    self.add_font("Unicode", "B", fonts["B"])
                self._font_family = "Unicode"
                self._unicode_enabled = True
            except Exception:
                return
        except Exception:
            return

    def set_report_font(self, style: str, size: int) -> None:
        use_style = style
        if self._unicode_enabled and style == "B":
            try:
                super().set_font(self._font_family, style, size)
                return
            except Exception:
                use_style = ""
        super().set_font(self._font_family, use_style, size)

    def txt(self, value: object) -> str:
        text = "" if value is None else str(value)
        return text if self._unicode_enabled else _pdf_safe(text)

    def header(self) -> None:
        self.set_y(8)
        left_x = 10

        if self._logo_path and Path(self._logo_path).exists():
            try:
                self.image(self._logo_path, x=left_x, y=8, w=18)
                left_x = 32
            except Exception:
                left_x = 10

        self.set_x(left_x)
        self.set_report_font("B", 16)
        self.cell(0, 10, self.txt(self._title), ln=1)
        self.ln(2)

    def footer(self) -> None:
        self.set_y(-15)
        self.set_report_font("", 9)
        self.cell(0, 10, f"{self.page_no()}", align="C")


def generate_pdf_report(
    *,
    output_path: str,
    title: str,
    search_url: str,
    summary: dict[str, object],
    deals_df: pd.DataFrame,
    district_chart_path: str | None = None,
    weekly_chart_path: str | None = None,
    logo_path: str | None = None,
    generated_at: dt.datetime | None = None,
    top_n: int = 5,
    threshold_pct: float | None = None,
) -> Path:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    generated_at = generated_at or dt.datetime.now()

    pdf = RentalReportPDF(title=title, logo_path=logo_path)
    pdf.add_page()

    pdf.set_report_font("", 11)
    summary_lines: list[str] = []
    summary_lines.append(f"Generálva: {generated_at:%Y-%m-%d %H:%M}")

    
    seg_min = summary.get("segment_min_area_m2")
    seg_max = summary.get("segment_max_area_m2")
    if seg_min is not None and seg_max is not None:
        summary_lines.append(
            f"Szegmens: {_fmt_int_hu(seg_min)}-{_fmt_int_hu(seg_max)} m²"
            if pdf._unicode_enabled
            else f"Szegmens: {_fmt_int_hu(seg_min)}-{_fmt_int_hu(seg_max)} m2"
        )

    summary_lines.append("Keresés:")
    summary_lines.extend(f"  {part}" for part in _wrap_long_text(search_url, max_len=90))
    total_count = summary.get("listing_count_total")
    seg_count = summary.get("listing_count")
    if total_count is not None and seg_count is not None:
        summary_lines.append(
            f"Hirdetések száma: {_fmt_int_hu(seg_count)}"
        )
    else:
        summary_lines.append(f"Hirdetések száma: {_fmt_int_hu(summary.get('listing_count'))}")
    summary_lines.append(f"Átlag bérleti díj: {_fmt_int_hu(summary.get('avg_price_huf'))} Ft/hó")
    summary_lines.append(
        (
            f"Átlag fajlagos ár: {_fmt_int_hu(summary.get('avg_price_per_sqm_huf'))} Ft/hó/m²"
            if pdf._unicode_enabled
            else f"Átlag fajlagos ár: {_fmt_int_hu(summary.get('avg_price_per_sqm_huf'))} Ft/ho/m2"
        )
    )

    if summary.get("cheapest_district") is not None:
        summary_lines.append(
            (
                f"Legolcsóbb kerület (átlag): {summary.get('cheapest_district')}. kerület (~{_fmt_int_hu(summary.get('cheapest_district_avg_pps'))} Ft/hó/m²)"
                if pdf._unicode_enabled
                else f"Legolcsobb kerulet (atlag): {summary.get('cheapest_district')}. (~{_fmt_int_hu(summary.get('cheapest_district_avg_pps'))} Ft/ho/m2)"
            )
        )
    if summary.get("most_expensive_district") is not None:
        summary_lines.append(
            (
                f"Legdrágább kerület (átlag): {summary.get('most_expensive_district')}. kerület (~{_fmt_int_hu(summary.get('most_expensive_district_avg_pps'))} Ft/hó/m²)"
                if pdf._unicode_enabled
                else f"Legdragabb kerulet (atlag): {summary.get('most_expensive_district')}. (~{_fmt_int_hu(summary.get('most_expensive_district_avg_pps'))} Ft/ho/m2)"
            )
        )

    def _district_line(item: object) -> str | None:
        if not isinstance(item, dict):
            return None
        d = item.get("district")
        mean = item.get("mean")
        count = item.get("count")
        if d is None or mean is None or count is None:
            return None
        try:
            d_int = int(round(float(d)))
        except Exception:
            return None
        return (
            f"{d_int}. kerület: ~{_fmt_int_hu(mean)} Ft/hó/m² (n={_fmt_int_hu(count)})"
            if pdf._unicode_enabled
            else f"{d_int}. kerulet: ~{_fmt_int_hu(mean)} Ft/ho/m2 (n={_fmt_int_hu(count)})"
        )

    cheapest = summary.get("district_top_cheapest")
    if isinstance(cheapest, list) and cheapest:
        summary_lines.append("Legolcsóbb kerületek (átlag):")
        for item in cheapest:
            line = _district_line(item)
            if line:
                summary_lines.append(f"  {line}")

    expensive = summary.get("district_top_expensive")
    if isinstance(expensive, list) and expensive:
        summary_lines.append("Legdrágább kerületek (átlag):")
        for item in expensive:
            line = _district_line(item)
            if line:
                summary_lines.append(f"  {line}")

    pdf.multi_cell(
        0,
        6,
        "\n".join(pdf.txt(line) for line in summary_lines),
    )
    pdf.set_x(pdf.l_margin)

    def _add_chart(path: str | None, caption: str) -> None:
        if not path:
            return
        p = Path(path)
        if not p.exists():
            return

        pdf.ln(3)
        pdf.set_report_font("B", 12)
        pdf.cell(0, 7, pdf.txt(caption), ln=1)
        try:
            pdf.image(str(p), w=190)
        except Exception as exc:
            logger.warning("Failed to embed image %s: %s", p, exc)

    _add_chart(district_chart_path, "Kerületi rangsor")
    _add_chart(weekly_chart_path, "Heti trend")

    pdf.ln(4)
    pdf.set_report_font("B", 13)
    pct_label = "15%+" if threshold_pct is None else f"{int(round(float(threshold_pct) * 100))}%+"
    deal_count = 0 if deals_df is None else int(len(deals_df))
    shown_n = min(int(top_n), deal_count) if deal_count else 0
    header_text = (
        f"Top {shown_n} legjobb ajánlat ({pct_label} az átlag alatt)"
        if shown_n
        else f"Top {int(top_n)} legjobb ajánlat ({pct_label} az átlag alatt)"
    )
    pdf.cell(0, 8, pdf.txt(header_text), ln=1)

    if deals_df is None or deals_df.empty:
        pdf.set_report_font("", 11)
        pdf.multi_cell(0, 6, pdf.txt("Nincs találat a deal-finder feltételek alapján."))
    else:
        show = deals_df.head(top_n).copy()
        for i, (_, row) in enumerate(show.iterrows(), start=1):
            listing_id = str(row.get("listing_id") or "")
            url = str(row.get("url") or "")
            district = row.get("district")
            area_m2 = row.get("area_m2")
            price_huf = row.get("price_huf")
            pps = row.get("price_per_sqm_huf")
            title_row = row.get("title") or row.get("location_text") or ""

            district_text = f"{int(district)}. kerület" if pd.notna(district) else "-"

            pdf.set_report_font("B", 11)
            pdf.multi_cell(0, 6, pdf.txt(f"{i}. {title_row}"))
            pdf.set_x(pdf.l_margin)
            pdf.set_report_font("", 10)
            pdf.multi_cell(
                0,
                5,
                pdf.txt(
                    f"{district_text} | {_fmt_int_hu(price_huf)} Ft/hó | {_fmt_float_hu(area_m2, digits=0)} m² | {_fmt_int_hu(pps)} Ft/hó/m²"
                    if pdf._unicode_enabled
                    else f"{district_text} | {_fmt_int_hu(price_huf)} Ft/ho | {_fmt_float_hu(area_m2, digits=0)} m2 | {_fmt_int_hu(pps)} Ft/ho/m2"
                ),
            )
            pdf.set_x(pdf.l_margin)

            if url and listing_id:
                pdf.set_report_font("", 10)
                pdf.cell(0, 5, pdf.txt(f"Hirdetés: {listing_id}"), ln=1, link=url)

            pdf.ln(2)

    pdf.output(str(out))
    logger.info("Saved PDF report: %s", out)
    return out
