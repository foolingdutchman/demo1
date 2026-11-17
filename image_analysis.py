import base64
from html import escape
from pathlib import Path
from typing import Any, Callable

from i18n import translate


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


def is_image_file(uploaded_file: Any) -> bool:
	"""Check whether an uploaded file is a supported image format."""
	if isinstance(uploaded_file, (str, Path)):
		filename = str(uploaded_file)
	else:
		filename = getattr(uploaded_file, "name", None) or getattr(
			uploaded_file, "filename", ""
		)
	return Path(filename).suffix.lower() in IMAGE_EXTENSIONS


def build_image_preview(filename: str, image_bytes: bytes, locale="zh-CN") -> str:
	extension = Path(filename).suffix.lower()
	mime_type = IMAGE_MIME_TYPES.get(extension)
	if mime_type is None:
		raise ValueError(
			translate(locale, "image.unsupported", extension=extension)
		)

	encoded_image = base64.b64encode(image_bytes).decode("ascii")
	preview_label = translate(locale, "preview.image")
	return (
		f'<section class="preview image-preview"><h2>{preview_label}</h2>'
		f'<img src="data:{mime_type};base64,{encoded_image}" '
		f'alt="{escape(Path(filename).name)}"></section>'
	)


def process_upload(uploaded_file: Any, image_analysis: Callable[[Any], Any]) -> Any:
	"""Pass image uploads to image_analysis; return None for other file types."""
	if is_image_file(uploaded_file):
		return image_analysis(uploaded_file)
	return None
