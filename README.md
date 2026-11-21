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
- `POST /api/auth` with JSON fields `mode`, `username`, `password`, and (for registration) `confirm_password`
- `POST /api/logout`
- `GET /api/uploads`, `POST /api/uploads` with multipart field `file`
- `GET /api/uploads/{id}` and `GET /api/uploads/{id}?analyze=1`
- `POST /api/uploads/{id}/ai-analysis` to analyze an uploaded file with the configured model API
- `DELETE /api/uploads/{id}`
- `GET /api/uploads/{id}/content` for previewable file content; add `?download=1` to download

The content endpoint returns the file bytes rather than JSON so browsers can display or download the original file.

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
