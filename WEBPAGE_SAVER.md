# Webpage saver

This companion tool saves ordinary webpages (not Xiaohongshu posts) as:

- the complete readable page text in its original language;
- a Chinese translation below the original;
- locally downloaded pictures referenced by the Markdown file.

## Easiest use on Windows

Double-click `save_webpage.bat`, paste a URL, and press Enter.

Output is written to `Volume/Webpages/<page title>/`. Each page gets one
Markdown file and an `images` folder.

You can also drag a `.txt` or `.md` URL-list file onto `save_webpage.bat`.

## Command line

```powershell
.\save_webpage.bat "https://example.com/article"
```

Or call Python directly:

```powershell
.\.venv\Scripts\python.exe .\scripts\save_webpage.py "https://example.com/article"
```

Useful options:

```text
-o FOLDER       Choose another output folder
--no-translate  Save only the original page
--headed         Show the browser for a page that needs interaction
--timeout 90     Allow 90 seconds for a slow page
```

Example with options:

```powershell
.\save_webpage.bat "https://example.com/article" --headed --timeout 90
```

Translation requires internet access and uses Google Translate's public web
endpoint; it does not require an API key. Pages behind a login or paywall can
only save content that the automated browser is allowed to see. If Chromium is
not installed yet, run:

```powershell
uv run playwright install chromium
```

The saver prints progress separately for page loading, image downloads,
translation, and Markdown writing. If Google Translate cannot be reached within
20 seconds, the page is still saved with its original text; use
`--no-translate` to skip that check entirely.
