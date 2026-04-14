from __future__ import annotations

import base64
from io import BytesIO


def render_html_to_pdf(html: str) -> bytes:
    """Convert an HTML string to PDF bytes using xhtml2pdf."""
    from xhtml2pdf import pisa

    buf = BytesIO()
    result = pisa.CreatePDF(html.encode("utf-8"), dest=buf, encoding="utf-8")
    if result.err:
        raise RuntimeError(f"PDF generation failed (error code {result.err})")
    return buf.getvalue()


_CHART_COLORS = [
    "#36a2eb", "#ff6384", "#4bc0c0", "#ffce56",
    "#9966ff", "#ff9f40", "#63ff84", "#c9cbcf",
    "#ff63ff", "#36eba2",
]


def render_chart_to_base64(chart_data: dict | None) -> str | None:
    """Render chart_data dict to a base64-encoded PNG for embedding in PDF."""
    if not chart_data or not chart_data.get("labels"):
        return None

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    labels = chart_data["labels"]
    datasets = chart_data.get("datasets") or []
    if not datasets:
        return None

    is_hours = chart_data.get("is_hours", False)

    primary = [ds for ds in datasets if not ds.get("yAxisID")]
    secondary = [ds for ds in datasets if ds.get("yAxisID")]

    fig, ax = plt.subplots(figsize=(10, 4))
    x = np.arange(len(labels))

    # --- primary bars ---
    n = len(primary)
    if n:
        w = 0.8 / n
        for i, ds in enumerate(primary):
            ci = datasets.index(ds) % len(_CHART_COLORS)
            offset = (i - n / 2 + 0.5) * w
            ax.bar(x + offset, ds["data"], w, label=ds["label"],
                   color=_CHART_COLORS[ci], alpha=0.75)

    ax.set_ylabel("Hours" if is_hours else "$")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)

    # --- secondary line axis (e.g. mechanic hours in general_revenue) ---
    if secondary:
        ax2 = ax.twinx()
        for ds in secondary:
            ci = datasets.index(ds) % len(_CHART_COLORS)
            ax2.plot(x, ds["data"], color=_CHART_COLORS[ci],
                     marker="o", linewidth=2, label=ds["label"])
        ax2.set_ylabel("Hours")
        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=7)
    else:
        ax.legend(loc="upper left", fontsize=7)

    plt.tight_layout()
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")
 