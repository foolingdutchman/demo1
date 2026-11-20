# File analysis app

The project is split into a static frontend and a Python JSON API:

- `frontend/` contains the browser pages and JavaScript.
- `api_server.py` contains the backend API. Uploaded files and accounts are stored in the root `uploads.db`.
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

The file details page keeps its local analysis button and provides a separate AI analysis action. The AI action sends extracted text, PDF text, Excel worksheet data, or supported image data to the configured OpenAI-compatible chat-completions API. The result is not persisted by the application. Scanned PDFs are not OCR-processed. Images must use JPEG, PNG, GIF, or WebP; the configured model must support vision input.

Configure the API key and model in the environment of the process that starts `web-python.py`; never put credentials in frontend files or commit them:

```powershell
$env:AI_API_BASE_URL = "https://api.openai.com/v1"
$env:AI_MODEL = "<provider model name>"
# Set AI_API_KEY using your secret-management or process-environment mechanism.
$env:AI_MAX_INPUT_CHARS = "60000"
python web-python.py
```

`AI_API_BASE_URL` defaults to the OpenAI API root. Remote endpoints must use HTTPS. The default extracted-text limit is 60,000 characters; larger documents are rejected rather than silently truncated. `AI_API_TIMEOUT_SECONDS` defaults to 60.

Run the API integration tests with:

```powershell
python -m unittest discover -s tests
```
