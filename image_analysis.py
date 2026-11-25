from pathlib import Path
from typing import Any, Callable
import json
import os
import subprocess
import tempfile

IMAGE_MIME_TYPES = {
	".jpg": "image/jpeg",
	".jpeg": "image/jpeg",
	".png": "image/png",
	".gif": "image/gif",
	".bmp": "image/bmp",
	".webp": "image/webp",
	".tif": "image/tiff",
	".tiff": "image/tiff",
}
IMAGE_EXTENSIONS = set(IMAGE_MIME_TYPES)


def build_image_preview(upload_id: int) -> dict[str, str]:
	return {"type": "image", "url": f"/api/uploads/{upload_id}/content"}


def build_image_analysis(locale: str) -> dict[str, str]:
	from i18n import translate

	return {
		"type": "image",
		"message": translate(locale, "analysis.image_pending"),
	}


def extract_document_ocr_text(document_bytes, extension):
	workspace = Path(__file__).resolve().parent
	python = workspace / ".venv-ocr" / "Scripts" / "python.exe"
	worker = workspace / "paddleocr_worker.py"
	if not python.is_file():
		raise FileNotFoundError(
			"PaddleOCR environment is missing; recreate .venv-ocr"
		)
	with tempfile.TemporaryDirectory() as temp_dir:
		input_path = Path(temp_dir) / f"document{extension}"
		output_path = Path(temp_dir) / "result.json"
		input_path.write_bytes(document_bytes)
		result = subprocess.run(
			[str(python), str(worker), str(input_path), str(output_path)],
			capture_output=True,
			timeout=300,
			check=False,
			env={**os.environ, "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True"},
		)
		if result.returncode != 0 or not output_path.is_file():
			stderr = (result.stderr or b"").decode(
				"utf-8", errors="replace"
			).strip()
			raise RuntimeError(stderr or "PaddleOCR failed")
		parsed = json.loads(output_path.read_text(encoding="utf-8"))
	return str(parsed.get("text") or "")


def extract_image_text(image_bytes, extension):
	return extract_document_ocr_text(image_bytes, extension)


def analyze_image_shipping_document(filename, image_bytes, locale):
	from pdf_analysis import analyze_shipping_document_text

	extension = Path(filename).suffix.lower()
	if extension not in IMAGE_MIME_TYPES:
		analysis = analyze_shipping_document_text("")
		analysis.update({
			"source": "image",
			"status": "unsupported_image",
		})
		return analysis
	try:
		text = extract_document_ocr_text(image_bytes, extension)
		analysis = analyze_shipping_document_text(text)
		analysis.update({
			"source": "image",
			"status": "recognized" if analysis["matched"] else "unrecognized",
		})
		return analysis
	except (
		FileNotFoundError,
		json.JSONDecodeError,
		OSError,
		RuntimeError,
		subprocess.SubprocessError,
	):
		analysis = analyze_shipping_document_text("")
		analysis.update({
			"source": "image",
			"status": "analysis_unavailable",
		})
		return analysis


def is_image_file(uploaded_file: Any) -> bool:
	"""Check whether an uploaded file is a supported image format."""
	if isinstance(uploaded_file, (str, Path)):
		filename = str(uploaded_file)
	else:
		filename = getattr(uploaded_file, "name", None) or getattr(
			uploaded_file, "filename", ""
		)
	return Path(filename).suffix.lower() in IMAGE_EXTENSIONS


def process_upload(uploaded_file: Any, image_analysis: Callable[[Any], Any]) -> Any:
	"""Pass image uploads to image_analysis; return None for other file types."""
	if is_image_file(uploaded_file):
		return image_analysis(uploaded_file)
	return None
