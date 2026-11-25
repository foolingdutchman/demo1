import json
import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault("FLAGS_use_mkldnn", "0")

from paddleocr import PaddleOCR


def main():
    if len(sys.argv) != 3:
        raise SystemExit("Usage: paddleocr_worker.py IMAGE OUTPUT")
    source_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    ocr = PaddleOCR(
        lang="ch",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )
    text_lines = []
    if source_path.suffix.lower() == ".pdf":
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(str(source_path))
        with tempfile.TemporaryDirectory() as rendered_dir:
            rendered_root = Path(rendered_dir)
            for index, page in enumerate(document):
                image_path = rendered_root / f"page-{index + 1}.png"
                page.render(scale=2).to_pil().save(image_path)
                for result in ocr.predict(str(image_path)):
                    lines = result.get("rec_texts") if hasattr(result, "get") else []
                    if lines:
                        text_lines.extend(
                            str(line).strip() for line in lines if str(line).strip()
                        )
    else:
        for result in ocr.predict(str(source_path)):
            lines = result.get("rec_texts") if hasattr(result, "get") else []
            if lines:
                text_lines.extend(
                    str(line).strip() for line in lines if str(line).strip()
                )
    output_path.write_text(
        json.dumps({"text": "\n".join(text_lines)}, ensure_ascii=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
