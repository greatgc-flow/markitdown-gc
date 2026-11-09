# MarkItDown GC

> Unofficial fork of [Microsoft MarkItDown](https://github.com/microsoft/markitdown), maintained at [greatgc-flow/markitdown-gc](https://github.com/greatgc-flow/markitdown-gc). This fork is not affiliated with or endorsed by Microsoft.
> Install `markitdown-gc` in a dedicated virtual environment. Never co-install upstream `markitdown` or plugins that depend on it: both ship `markitdown/` and overwrite shared files. The CLI exits 1 on detected conflicts; library import warns once. Recreate mixed environments; see `markitdown-gc --guide install`.

Optional local LibreOffice converts legacy `.doc`, `.ppt`, `.rtf` and HWP < 5.0: enable `--libreoffice`, Python `MarkItDown(libreoffice_path="soffice")`, or `SOFFICE_PATH`. Output carries a LibreOffice version/source-extension comment with `fidelity=DERIVED`. See `--guide formats`, `--guide install` and `--guide errors`.

> [!TIP]
> MarkItDown is a Python package and command-line utility for converting various files to Markdown (e.g., for indexing, text analysis, etc).
>
> For more information, and full documentation, see the project [README.md](https://github.com/greatgc-flow/markitdown-gc) on GitHub.

> [!IMPORTANT]
> MarkItDown performs I/O with the privileges of the current process. Like open() or requests.get(), it will access resources that the process itself can access. Sanitize your inputs in untrusted environments, and call the narrowest `convert_*` function needed for your use case (e.g., `convert_stream()`, or `convert_local()`). See the [Security Considerations](https://github.com/greatgc-flow/markitdown-gc#security-considerations) section of the documentation for more information.

## Installation

From PyPI:

```bash
pip install markitdown-gc
```

All document converters (PDF, DOCX, PPTX, XLSX/XLS, Outlook, HWP5/HWPX) are included in the default install; `markitdown-gc --guide` explains usage. Audio, YouTube and Azure support stay optional (`[all]` adds them). The backend is pure Python for all platforms. Tables use pipe rows (merged cells lose their spans), images become `[IMAGE]` placeholders, and note bodies are included. HWP < 5, encrypted/distribution documents, headers/footers, and exact layout are unsupported. See the project [HWP/HWPX documentation](https://github.com/greatgc-flow/markitdown-gc#hwphwpx) for details.

From source:

```bash
git clone https://github.com/greatgc-flow/markitdown-gc.git
cd markitdown-gc
pip install -e packages/markitdown
```

## Usage

### Command-Line

```bash
markitdown-gc path-to-file.pdf > document.md
```

### Python API

```python
from markitdown import MarkItDown

md = MarkItDown()
result = md.convert("test.xlsx")
print(result.markdown)
```

### More Information

For more information, and full documentation, see the project [README.md](https://github.com/greatgc-flow/markitdown-gc) on GitHub.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft
trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's policies.
