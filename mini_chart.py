import io
import yfinance as yf
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def make_mini_chart(ticker: str, days: int = 7) -> bytes:
    """Download recent price history and return a PNG chart as bytes."""
    tk = yf.Ticker(ticker)
    hist = tk.history(period=f"{days}d")

    fig, ax = plt.subplots(figsize=(4, 2), dpi=100)
    ax.plot(hist.index, hist["Close"], color="#00aaff", linewidth=1.5)
    ax.fill_between(hist.index, hist["Close"], alpha=0.15, color="#00aaff")
    ax.set_title(ticker, fontsize=9, color="white", pad=3)
    ax.tick_params(axis="both", labelsize=6, colors="gray")
    ax.spines[:].set_visible(False)
    ax.set_facecolor("#111111")
    fig.patch.set_facecolor("#111111")
    plt.tight_layout(pad=0.4)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.read()
