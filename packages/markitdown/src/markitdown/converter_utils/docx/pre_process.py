import struct
import zipfile
from io import BytesIO
from typing import BinaryIO
from xml.etree import ElementTree
from lxml import etree

from .math.omml import OMML_NS, oMath2Latex

_WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _parse_part(content: bytes):
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    return etree.fromstring(content, parser=parser)


def _serialize_part(root) -> bytes:
    return etree.tostring(root.getroottree(), encoding="utf-8", xml_declaration=True)


def _pre_process_strike(content: bytes) -> bytes:
    if b"dstrike" not in content:
        return content
    root = _parse_part(content)
    strikes = list(root.iter(_WORD + "dstrike"))
    for strike in strikes:
        strike.tag = _WORD + "strike"
    return _serialize_part(root) if strikes else content


def _math_run(math, fence):
    run = etree.Element(_WORD + "r")
    text = etree.SubElement(run, _WORD + "t")
    equation = ElementTree.fromstring(etree.tostring(math, with_tail=False))
    text.text = fence + oMath2Latex(equation).latex + fence
    return run


def _pre_process_math(content: bytes) -> bytes:
    """Modify only namespaced equation nodes; retain the original document and relationship namespaces."""
    if b"oMath" not in content:
        return content
    root = _parse_part(content)
    changed = False
    for block in list(root.iter(OMML_NS + "oMathPara")):
        parent = block.getparent()
        if parent is None:
            continue
        paragraph = etree.Element(_WORD + "p")
        for math in block.iter(OMML_NS + "oMath"):
            paragraph.append(_math_run(math, "$$"))
        paragraph.tail = block.tail
        parent.replace(block, paragraph)
        changed = True
    for math in list(root.iter(OMML_NS + "oMath")):
        parent = math.getparent()
        if parent is not None:
            run = _math_run(math, "$")
            run.tail = math.tail
            parent.replace(math, run)
            changed = True
    return _serialize_part(root) if changed else content


def _fix_zip_filename_casing(input_docx: BinaryIO) -> BinaryIO:
    """
    Fix ZIP files where local file header filenames differ in casing
    from the central directory filenames.

    Some document generators (e.g. certain Microsoft Word versions,
    legal document systems) produce .docx/.pptx files where the central
    directory records one casing (e.g. 'customXml/item2.xml') but
    the local file headers record another (e.g. 'customXML/item2.xml').
    Python's zipfile module raises BadZipFile when reading such files.

    This function patches local file header filenames to match the
    central directory, which is the authoritative source used by
    zipfile.ZipFile.
    """
    input_docx.seek(0)
    raw = bytearray(input_docx.read())

    # Read the central directory to get authoritative filenames
    try:
        with zipfile.ZipFile(BytesIO(raw), "r") as zf:
            cd_entries = {
                zi.header_offset: (zi.orig_filename, zi.flag_bits)
                for zi in zf.infolist()
            }
    except zipfile.BadZipFile:
        # Can't even read central directory — return as-is, let it fail later
        input_docx.seek(0)
        return input_docx

    patched = False
    for offset, (cd_name, flag_bits) in cd_entries.items():
        # Verify local file header signature
        if offset + 30 > len(raw) or raw[offset : offset + 4] != b"PK\x03\x04":
            continue

        local_fname_len = struct.unpack_from("<H", raw, offset + 26)[0]
        if offset + 30 + local_fname_len > len(raw):
            continue

        local_name = bytes(raw[offset + 30 : offset + 30 + local_fname_len])
        # ZIP filenames are cp437 unless flag bit 11 marks them as UTF-8, which is
        # how zipfile decoded orig_filename in the first place.
        central_name = cd_name.encode("utf-8" if flag_bits & 0x800 else "cp437")

        # Only patch if lengths match but content differs (casing mismatch)
        if (
            local_name != central_name
            and len(local_name) == len(central_name)
            and local_name.lower() == central_name.lower()
        ):
            raw[offset + 30 : offset + 30 + local_fname_len] = central_name
            patched = True

    if patched:
        return BytesIO(bytes(raw))
    input_docx.seek(0)
    return input_docx


def _pre_process_styles(content: bytes) -> bytes:
    """
    Repairs DOCX style definitions that Mammoth cannot read.

    Mammoth indexes ``w:type`` and ``w:styleId`` directly, so a ``w:style``
    element missing either attribute causes conversion to fail with a
    ``KeyError`` before any document text can be extracted.

    ``w:type`` is optional in OOXML: when it is absent the style type defaults
    to ``paragraph``, so the attribute is filled in rather than dropping the
    style, which would discard its formatting (a heading would be emitted as
    plain body text). A style with no ``w:styleId`` cannot be referenced by the
    document body, so it is removed. Double strikethrough is normalized to
    single strikethrough in the same namespace-aware pass.

    Match elements and attributes by namespace URI, preserving their qualified
    names when repairing the XML. Return the original bytes if no repair is needed.
    """
    namespace = _WORD
    root = _parse_part(content)
    changed = False

    for style in root.findall(namespace + "style"):
        if namespace + "styleId" not in style.attrib:
            root.remove(style)
            changed = True
        elif namespace + "type" not in style.attrib:
            style.set(namespace + "type", "paragraph")
            changed = True

    for strike in root.iter(namespace + "dstrike"):
        strike.tag = namespace + "strike"
        changed = True

    if not changed:
        return content

    return etree.tostring(root.getroottree(), encoding="utf-8", xml_declaration=True)


def pre_process_docx(input_docx: BinaryIO) -> BinaryIO:
    """
    Pre-processes a DOCX file with provided steps.

    The process works by unzipping the DOCX file in memory, transforming specific XML files
    (such as converting OMML elements to LaTeX), and then zipping everything back into a
    DOCX file without writing to disk.

    Args:
        input_docx (BinaryIO): A binary input stream representing the DOCX file.

    Returns:
        BinaryIO: A binary output stream representing the processed DOCX file.
    """
    # Fix ZIP filename casing mismatch before any processing
    input_docx = _fix_zip_filename_casing(input_docx)

    output_docx = BytesIO()
    # The pre-processing steps to apply to each file in the .docx
    pre_process_enable_files = {
        "word/document.xml": (_pre_process_strike, _pre_process_math),
        "word/footnotes.xml": (_pre_process_strike, _pre_process_math),
        "word/endnotes.xml": (_pre_process_strike, _pre_process_math),
        "word/styles.xml": (_pre_process_styles,),
    }
    with zipfile.ZipFile(input_docx, mode="r") as zip_input:
        files = {name: zip_input.read(name) for name in zip_input.namelist()}
        with zipfile.ZipFile(output_docx, mode="w") as zip_output:
            zip_output.comment = zip_input.comment
            for name, content in files.items():
                updated_content = content
                # Each step is applied independently, so one failing step does
                # not discard the results of the others.
                for pre_process_step in pre_process_enable_files.get(name, ()):
                    try:
                        updated_content = pre_process_step(updated_content)
                    except Exception:
                        pass
                zip_output.writestr(name, updated_content)
    output_docx.seek(0)
    return output_docx
