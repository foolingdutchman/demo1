# File analysis app

The project is split into a static frontend and a Python JSON API:

- `frontend/` contains the browser pages and JavaScript.
- `api_server.py` handles API routes; `auth_manager.py` contains registration, login, and session handling.
- `image_analysis.py`, `excel_analysis.py`, and `ai_analysis.py` contain format-specific analysis.
- `outcall_api.py` retrieves and parses public financial market data; source URLs are configurable module constants.
- `db_manager.py` owns SQLite schema setup and database operations. Uploaded files, accounts, and quotations are stored in the root `uploads.db`.
- `frontend/index.html` is the public landing page. Sign in or register to open `frontend/assets.html`, the authenticated upload workspace.

## Run locally

Open two PowerShell terminals from the project directory. Keep both running while using the app.

Start the API:

```powershell
python web-python.py
```

Start the frontend:

```powershell
.\frontend\start.ps1
```

Open `http://127.0.0.1:5500`. The frontend script serves only the static files; the API must also be running. Its address is configured in `frontend/api-config.js`. If PowerShell blocks the script, run it with `powershell -ExecutionPolicy Bypass -File .\frontend\start.ps1`. If the frontend is hosted on another origin, set the backend `CORS_ORIGINS` environment variable to a comma-separated list of allowed origins before starting the API.

## API

All application data endpoints return JSON. Errors use the shape `{"error":{"code":"...","message":"..."}}`. The API uses an HTTP-only session cookie, so browser requests include credentials.

- `GET /api/health`, `GET /api/translations?locale=zh-CN`, `GET /api/session`
- `POST /api/sudan-cotton-pricing` to calculate a Sudan cotton CIF Qingdao purchase model from string-valued JSON inputs
- `POST /api/auth` with JSON fields `mode`, `username`, `password`, and (for registration) `confirm_password`
- `POST /api/logout`
- `GET /api/uploads`, `POST /api/uploads` with multipart field `file`
- `GET /api/uploads/{id}` and `GET /api/uploads/{id}?analyze=1`
- `POST /api/uploads/{id}/ai-analysis` to analyze an uploaded file with the configured model API
- `DELETE /api/uploads/{id}`
- `GET /api/uploads/{id}/content` for previewable file content; add `?download=1` to download

The content endpoint returns the file bytes rather than JSON so browsers can display or download the original file.

## Sudan cotton purchase model

The cotton market board and detail pages show the latest CC Index 3128B, 2227B, and 2129B quotations from the public China Cotton Information Network homepage, including the source quote date. Its historical detail page requires login, so no CC Index history is fabricated or displayed. CC Index and Zhengzhou cotton prices are shown in CNY/tonne.

The Zhengzhou cotton detail page includes a parameterized CIF Qingdao model for Sudan cotton. The domestic selling benchmark can use CC Index 3128B, 2227B (the default), 2129B, Zhengzhou cotton futures, or a manually entered price; the user can also enter an actual mill bid and a CIF or FOB supplier offer (with freight, insurance, and other CIF costs for FOB conversion). The model separately reports economic profit, maximum CIF for a target profit, break-even CIF, recoverable import VAT and estimated import-stage cash funding, with domestic-price, FX, quota-cost, and supplier-quote sensitivities. Sudan cotton USD/tonne prices are also converted to US cents per pound (USC/lb).

The defaults (including a CNY 500/tonne quality discount, CNY 4,200/tonne quota cost, 0% duty, and 100% recoverable import VAT) are editable case assumptions, not verified market or tariff facts. Confirm the shipment's actual HS code and quota treatment, tax documentation, HVI quality, buyer bids, and settlement FX before relying on a result. Any domestic index or futures price is only a provisional benchmark, not an actual Sudan cotton sale price.

## AI file analysis

The file details page keeps its local analysis button and provides a separate AI analysis action. The AI action sends extracted text, PDF text, Excel worksheet data, or supported image data to the configured OpenAI-compatible chat-completions API. Image prompts are tuned to recognize cotton quality reports and red-marked prices, following the workflow in [cotton-quotation-ocr](https://github.com/foolingdutchman/cotton-quotation-ocr); other image types are analyzed normally. This uses the configured vision model rather than installing the repository's Tesseract workflow. AI analysis results are saved with the uploaded file and automatically displayed when its detail page is reopened. Running AI analysis again replaces the previous result. Scanned PDFs are not OCR-processed. Images must use JPEG, PNG, GIF, or WebP; the configured model must support vision input.

Save the AI settings in a local `.env` file next to `api_server.py`. Copy the example file, edit it once to add your API key, then start the server as usual:

```powershell
Copy-Item .env.example .env
notepad .env
python web-python.py
```

The `.env` file is ignored by Git. It contains the API key in plain text, so keep it on a trusted local machine and never commit or share it. Existing process environment variables take precedence over values in `.env`. The defaults are `https://api.minimax.cn/v1` for `AI_API_BASE_URL`, `MiniMax-M3.1-Flash` for `AI_MODEL`, and 60,000 characters for `AI_MAX_INPUT_CHARS`. Larger documents are rejected rather than silently truncated. `AI_API_TIMEOUT_SECONDS` defaults to 60. Remote endpoints must use HTTPS.

Run the API integration tests with:

```powershell
python -m unittest discover -s tests
```
