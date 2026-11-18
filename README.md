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
- `DELETE /api/uploads/{id}`
- `GET /api/uploads/{id}/content` for previewable file content; add `?download=1` to download

The content endpoint returns the file bytes rather than JSON so browsers can display or download the original file.

Run the API integration tests with:

```powershell
python -m unittest discover -s tests
```
