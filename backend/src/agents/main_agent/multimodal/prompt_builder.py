"""
Prompt builder for multimodal content (text, images, documents)
"""
import logging
import base64
from typing import List, Optional, Union, Dict, Any
from agents.main_agent.multimodal.image_handler import ImageHandler
from agents.main_agent.multimodal.document_handler import DocumentHandler
from agents.main_agent.multimodal.file_sanitizer import FileSanitizer

logger = logging.getLogger(__name__)


class PromptBuilder:
    """Builds prompts with multimodal content support"""

    def __init__(self):
        """Initialize prompt builder with handlers"""
        self.image_handler = ImageHandler()
        self.document_handler = DocumentHandler()
        self.file_sanitizer = FileSanitizer()

    def build_prompt(
        self,
        message: str,
        files: Optional[List[Any]] = None
    ) -> Union[str, List[Dict[str, Any]]]:
        """
        Build prompt for Strands Agent with multimodal support

        Args:
            message: User message text
            files: Optional list of FileContent objects with base64 bytes

        Returns:
            str or list[ContentBlock]: Simple string or multimodal content blocks
        """
        # If no files, return simple text
        if not files or len(files) == 0:
            return message

        # Build ContentBlock list for multimodal input
        content_blocks = []

        # Add text first (with file reference marker for session history reconstruction)
        file_names = [f.filename for f in files if hasattr(f, 'filename')]
        if file_names:
            # Add file reference marker after user message for session history
            text_with_marker = f"{message}\n\n[Attached files: {', '.join(file_names)}]"
            content_blocks.append({"text": text_with_marker})
        else:
            content_blocks.append({"text": message})

        # Add each file as appropriate ContentBlock
        for file in files:
            content_block = self._process_file(file)
            if content_block:
                content_blocks.append(content_block)

        return content_blocks

    # Extensions that are plain text — decode directly, no parser needed
    _TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".html", ".htm"}

    # Binary document extensions that require a parser for text extraction
    _BINARY_DOC_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx"}

    def _process_file(self, file: Any) -> Optional[Dict[str, Any]]:
        """
        Process a single file and create appropriate ContentBlock.

        Images → Strands image block (converted to image_url for OpenAI providers).
        Plain-text files → inline text block (universally compatible).
        Binary docs → inline text block with extracted text when possible, or a
            placeholder note for formats that require an uninstalled parser.
        """
        content_type = file.content_type.lower()
        filename = file.filename
        filename_lower = filename.lower()

        file_bytes = base64.b64decode(file.bytes)

        # --- Images ---
        if self.image_handler.is_image(content_type, filename_lower):
            return self.image_handler.create_content_block(
                file_bytes=file_bytes,
                content_type=content_type,
                filename=filename_lower,
            )

        # --- Plain-text documents: decode and inline ---
        if any(filename_lower.endswith(ext) for ext in self._TEXT_EXTENSIONS):
            try:
                text = file_bytes.decode("utf-8", errors="replace")
            except Exception:
                text = file_bytes.decode("latin-1", errors="replace")
            return {"text": f"<file name=\"{filename}\">\n{text}\n</file>"}

        # --- PDF: try pypdf if available, otherwise note ---
        if filename_lower.endswith(".pdf"):
            try:
                import pypdf  # type: ignore
                import io
                reader = pypdf.PdfReader(io.BytesIO(file_bytes))
                pages = [page.extract_text() or "" for page in reader.pages]
                text = "\n\n".join(p for p in pages if p.strip())
                return {"text": f"<file name=\"{filename}\">\n{text}\n</file>"}
            except ImportError:
                logger.warning(
                    "pypdf not installed — PDF text extraction unavailable. "
                    "Install with: uv add pypdf"
                )
                return {
                    "text": (
                        f'[File "{filename}" attached — PDF text extraction requires '
                        f"the pypdf package. Run `uv add pypdf` in the backend to enable it.]"
                    )
                }
            except Exception as exc:
                logger.warning("PDF extraction failed for %s: %s", filename, exc)
                return {"text": f'[File "{filename}" attached — PDF could not be parsed: {exc}]'}

        # --- Other binary formats (DOCX, XLSX, etc.) ---
        if self.document_handler.is_document(filename_lower):
            logger.warning("Binary document type not yet extractable: %s", filename)
            return {
                "text": (
                    f'[File "{filename}" attached — text extraction for this format is not yet supported. '
                    f"Consider converting to PDF or plain text.]"
                )
            }

        logger.warning("Unsupported file type: %s (%s)", filename, content_type)
        return None

    def get_content_type_summary(self, prompt: Union[str, List[Dict[str, Any]]]) -> str:
        """
        Get a summary of content types in the prompt

        Args:
            prompt: Prompt (string or content blocks)

        Returns:
            str: Summary description (e.g., "text only", "text + 2 images + 1 document")
        """
        if isinstance(prompt, str):
            return "text only"

        if isinstance(prompt, list):
            text_count = sum(1 for block in prompt if "text" in block)
            image_count = sum(1 for block in prompt if "image" in block)
            document_count = sum(1 for block in prompt if "document" in block)

            parts = []
            if text_count > 0:
                parts.append("text")
            if image_count > 0:
                parts.append(f"{image_count} image{'s' if image_count > 1 else ''}")
            if document_count > 0:
                parts.append(f"{document_count} document{'s' if document_count > 1 else ''}")

            return " + ".join(parts) if parts else "empty"

        return "unknown"
