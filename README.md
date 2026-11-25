# File analysis app

The project is split into a static frontend and a Python JSON API:

- `frontend/` contains the browser pages and JavaScript.
- `api_server.py` handles API routes; `auth_manager.py` contains registration, login, and session handling.
- `image_analysis.py`, `excel_analysis.py`, and `ai_analysis.py` contain format-specific analysis.
- `outcall_api.py` retrieves and parses public financial market data; source URLs are configurable module constants.
- `db_manager.py` owns SQLite schema setup and database operations. Uploaded files, accounts, and quotations are stored in the root `uploads.db`.
- Imported cotton quotations display the file's stored upload date (UTC, `YYYY-MM-DD`) as `quotation_date`. Existing undated rows with a surviving upload are backfilled at startup; template rows and rows whose upload is unavailable remain undated.
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

## Local document OCR

Order-related document images are recognized locally with PaddleOCR, not through the AI analysis API. Because PaddlePaddle does not publish Windows wheels for the project's Python 3.15 runtime, install its CPU environment with Python 3.12:

```powershell
py -3.12 -m venv .venv-ocr
.\.venv-ocr\Scripts\python.exe -m pip install -r requirements-ocr.txt
```

The main API keeps using `.venv` and invokes `.venv-ocr\Scripts\python.exe` for image OCR. OCR models are downloaded and cached on first use. No API key is required for related-document image recognition.

## API

All application data endpoints return JSON. Errors use the shape `{"error":{"code":"...","message":"..."}}`. The API uses an HTTP-only session cookie, so browser requests include credentials.

- `GET /api/health`, `GET /api/translations?locale=zh-CN`, `GET /api/session`
- `POST /api/sudan-cotton-pricing` to calculate a Sudan cotton CIF Qingdao purchase model from string-valued JSON inputs
- `POST /api/auth` with JSON fields `mode`, `username`, `password`, and (for registration) `confirm_password`
- `POST /api/logout`
- `GET /api/uploads`, `POST /api/uploads` with multipart field `file`
- `GET /api/cotton-quotations`, `PATCH /api/cotton-quotations/{row_number}` to list quotations and update editable fields with string values
- `GET /api/orders` to list the signed-in user's orders, newest first; `POST /api/orders` to create an order from quotation fields and Sudan cotton pricing inputs
- `GET`, `PATCH`, `DELETE /api/orders/{id}` to view, edit, or delete an order owned by the signed-in user. `PATCH` requires the complete quotation snapshot, pricing inputs, benchmark source, and delivery date; it recalculates the saved result.
- `GET /api/uploads/{id}` and `GET /api/uploads/{id}?analyze=1`
- `POST /api/uploads/{id}/ai-analysis` to analyze an uploaded file with the configured model API
- `DELETE /api/uploads/{id}`
- `GET /api/uploads/{id}/content` for previewable file content; add `?download=1` to download

The content endpoint returns the file bytes rather than JSON so browsers can display or download the original file.

The cotton detail page shows the latest five quotations ordered by quotation date, then insertion order; undated rows appear last. The management page shows all quotations and whether each has been ordered. Every signed-in user can view the shared list, but only the original file uploader or manual quotation creator can edit it; template rows and rows whose uploaded file was deleted are read-only. Source file metadata cannot be edited.

The Sudan cotton model can open `frontend/book_order.html` with its current inputs. Supplier prices are entered in USC/lb and converted to USD/tonne for the API, stored snapshots, and calculations (1 USC/lb = 22.0462 USD/tonne); freight and insurance remain USD/tonne. Quotation date and delivery date (`YYYY-MM-DD`) are required. If a selected quotation has no date, the user supplies a date for the order without changing the source quotation. Existing quotations are otherwise read-only in the order form and cannot be ordered twice. The purchase quantity is fixed to a positive quotation net weight, or otherwise its positive standard weight; if neither is available it can be entered manually. A manually entered quotation is saved on order creation, with net weight set to the purchase quantity, and can be edited by its creator. Order creation and quotation linking are atomic; the server uses the saved quotation rather than client-supplied values. The server validates and recalculates the model and saves its inputs and results alongside the signed-in user and quotation snapshot; creating an order does not submit a trade externally. `frontend/order.html` lists the signed-in user's orders, and `frontend/order_detail.html` displays and edits each order's snapshot without changing the original quotation. Deleting an order keeps the quotation and releases its ordered status. Existing orders without a delivery date retain a blank value until edited.

## Sudan cotton purchase model

The cotton market board and cotton detail pages (ICE, Zhengzhou and CC Index) show the latest CC Index 3128B, 2227B, and 2129B quotations from the public China Cotton Information Network homepage, including the source quote date. Its historical detail page requires login, so no CC Index history is fabricated or displayed. CC Index and Zhengzhou cotton prices are shown in CNY/tonne.

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
