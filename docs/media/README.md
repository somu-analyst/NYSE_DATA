# Demo media

Drop your demo recordings here, then they render in the main `README.md`.

## Recommended files
| File | What to record | Tip |
|------|----------------|-----|
| `telegram-demo.gif` | A Telegram session: `/menu`, `/gex SPY`, `/hiprob`, a scanner result | Keep it 10–20s, loopable |
| `dashboard-demo.gif` | Streamlit terminal: Market Overview → OI Analytics → Signal Accuracy | Show one full "story" |
| `gex-profile.png` / `dashboard-home.png` | Static screenshots for quick context | High-res, dark theme |

## How to capture
1. Record the screen (Windows: **Win+Alt+R** Game Bar, or [ScreenToGif](https://www.screentogif.com/) / OBS).
2. For autoplay-in-README, export/convert to **GIF** (ScreenToGif does this directly). Keep each GIF < ~10 MB.
3. Save into this folder with the names above.

## Two ways to embed in README
- **GIF (committed, autoplays):** already wired in `README.md` as
  `![Telegram demo](docs/media/telegram-demo.gif)` — just add the file and push.
- **MP4 with player (higher quality, not stored in repo):** open `README.md` in the
  GitHub web editor and **drag-and-drop** the `.mp4` into the Demo section. GitHub
  uploads it to its CDN and inserts a `https://github.com/user-attachments/...`
  link that renders as a video player (≤10 MB on free accounts).

> Tip: GIFs are the most reliable for viewers (autoplay, no click). Use MP4 drag-drop
> only if you need audio or longer/high-fidelity clips.
