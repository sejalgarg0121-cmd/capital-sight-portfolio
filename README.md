# Portfolio Hub — 10 Portfolio Streamlit App

A lightweight portfolio dashboard designed for Streamlit Community Cloud.

## Included

- Exactly 10 portfolio slots
- Unlimited holdings per portfolio (no hard-coded stock limit)
- Public read-only mode
- Admin login for editing
- Buy / Sell transaction entry
- Average buy price calculation
- Current value / P&L calculation
- Lazy price refresh with 5-minute caching
- Dark, minimal interface inspired by the supplied screenshots
- JSON data storage for a simple prototype

## Important persistence note

Streamlit Community Cloud is not a database. The included JSON file is intentionally simple for a prototype, but changes made to a deployed app's local filesystem should NOT be treated as durable production storage.

For a serious public product, replace the JSON data layer with Google Sheets, Supabase, PostgreSQL, or another persistent database.

## Admin login

For local development create:

`.streamlit/secrets.toml`

with:

```toml
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "your-strong-password"
```

For Streamlit Community Cloud, paste the same values into the app's Secrets settings.

## Deploy

1. Create a GitHub repository.
2. Upload:
   - `app.py`
   - `requirements.txt`
   - `.streamlit/config.toml`
   - `data/portfolio_data.json`
   - `.gitignore`
3. Go to Streamlit Community Cloud.
4. Create app.
5. Select the repository and `app.py`.
6. Add your secrets in Advanced settings / Secrets.
7. Deploy.

## Speed choices

The app deliberately does NOT fetch stock prices when it opens.

Prices are fetched only when the admin clicks **Refresh Prices**, and the fetched values are cached for 5 minutes. This avoids making 10 portfolios × many stocks wait on Yahoo Finance during every page load.

## Next upgrade

If you want this to become a reliable public product, the first upgrade should be persistent storage rather than adding more UI.
