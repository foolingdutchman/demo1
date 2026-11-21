from pathlib import Path
from typing import Any, Callable

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
