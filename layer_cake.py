"""
PDF defensive text injection utility.

Key features:
1. Smart position selection: inject only into text blocks whose width and x-position
   both fall within tolerance ranges, including support for two-column papers.
2. Smart font reuse: analyze all fonts used on the current page and reuse the
   most frequently used font for injected text.
3. Automatic wrapping: wrap injected text to the target width so it stays within bounds.
4. Invisible text: use Text Rendering Mode 3 (3 Tr) so the text is not rendered
   visually but remains extractable.

Dependency: pikepdf
"""

import re
import random
import json
import pikepdf
from pikepdf import Stream, Array
from typing import Tuple, List, Optional

from configuration.venue_config import get_venue_config


# =============================================================================
#                           Defensive prompt loading
# =============================================================================

def load_defensive_prompt_pool(filepath: str = "./configuration/defensive_prompt_pool.json") -> List[str]:
    """
    Load the defensive prompt pool from a JSON file.

    Args:
        filepath: Path to the JSON file.

    Returns:
        A list of prompt contents.
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return [item['content'] for item in data]

# =============================================================================
#                              Constant definitions
# =============================================================================

# Average character width by font family (unit: 1/1000 em)
_AVG_WIDTH_UNITS = {
    "Helvetica": 500,
    "Times-Roman": 500,
    "Courier": 600,
}


# =============================================================================
#                           Basic utility functions
# =============================================================================

def safe_get_object(obj):
    """
    Safely dereference a PDF object.

    Objects in a PDF may be indirect references. This helper repeatedly
    dereferences the object to retrieve the actual value, up to 10 times
    to avoid infinite loops.

    Args:
        obj: A PDF object or indirect reference.

    Returns:
        The dereferenced PDF object.
    """
    for _ in range(10):
        try:
            obj = obj.get_object()
        except Exception:
            break
    return obj


def get_page_dimensions(page_obj: pikepdf.Page) -> Tuple[float, float, float, float]:
    """
    Get page boundary dimensions.

    CropBox is preferred. MediaBox is used as a fallback.

    Args:
        page_obj: The page object.

    Returns:
        (x_min, y_min, x_max, y_max) page boundary coordinates.
    """
    # Prefer CropBox, then MediaBox.
    box = page_obj.get("/CropBox") or page_obj.get("/MediaBox")
    if box:
        try:
            return (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
        except (IndexError, TypeError, ValueError):
            pass
    # Default to common Letter size.
    return (0.0, 0.0, 612.0, 792.0)


def _escape_pdf_literal(s: str) -> str:
    """
    Escape special characters in a PDF literal string.

    Backslashes and parentheses must be escaped inside PDF strings.

    Args:
        s: The original string.

    Returns:
        The escaped string.
    """
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


# =============================================================================
#                           Font and resource management
# =============================================================================

def ensure_font_from_pool(pdf_obj: pikepdf.Pdf, page_obj: pikepdf.Page,
                          base_font: str, preferred_name: str = None) -> str:
    """
    Ensure that the page resources contain the requested font.

    If the font already exists on the page, reuse it. Otherwise create it.
    Standard Type1 fonts are supported.

    Args:
        pdf_obj: The PDF document object.
        page_obj: The page object.
        base_font: Base font name, such as "Helvetica".
        preferred_name: Preferred resource name for the font.

    Returns:
        The font resource name, such as "/F_H".
    """
    # Ensure the Resources and Font dictionaries exist.
    resources = page_obj.get("/Resources")
    if resources is None:
        resources = pikepdf.Dictionary()
        page_obj["/Resources"] = resources
    fonts = resources.get("/Font")
    if fonts is None:
        fonts = pikepdf.Dictionary()
        resources["/Font"] = fonts

    # Check whether the font already exists.
    for res_name, res_obj in fonts.items():
        try:
            val = safe_get_object(res_obj)
            if isinstance(val, pikepdf.Dictionary):
                existing_basefont = val.get("/BaseFont")
                if existing_basefont and str(existing_basefont) == f"/{base_font}":
                    return str(res_name)
        except Exception:
            continue

    # Generate a font resource name.
    font_name_map = {
        "Helvetica": "/F_H",
        "Courier": "/F_C",
        "Times-Roman": "/F_T",
        "ZapfDingbats": "/F_Z"
    }

    if preferred_name and preferred_name not in fonts:
        name = preferred_name
    else:
        base_name = font_name_map.get(base_font, f"/F_{base_font[:3]}")
        name = base_name
        if name in fonts:
            for i in range(1, 200):
                cand = f"{base_name}_{i}"
                if cand not in fonts:
                    name = cand
                    break

    # Create the font dictionary.
    font_dict = {
        "/Type": "/Font",
        "/Subtype": "/Type1",
        "/BaseFont": f"/{base_font}",
    }
    if base_font in ["Helvetica", "Courier", "Times-Roman"]:
        font_dict["/Encoding"] = "/WinAnsiEncoding"

    fonts[name] = pdf_obj.make_indirect(pikepdf.Dictionary(font_dict))
    return name


# =============================================================================
#                           Text measurement and wrapping
# =============================================================================

def estimate_text_width(text: str, family: str, size: float) -> float:
    """
    Estimate the width of text for a given font family and size.

    This uses predefined average character widths and is suitable for
    coarse width estimation.

    Args:
        text: Text content.
        family: Font family name.
        size: Font size in points.

    Returns:
        Estimated width in points.
    """
    units = _AVG_WIDTH_UNITS.get(family, 500)
    return len(text) * (units / 1000.0) * size


def wrap_text_to_width(text: str, family: str, size: float, max_width: float) -> List[str]:
    """
    Wrap text to a maximum width.

    Wrapping is done on word boundaries whenever possible so that each line
    stays close to, but does not exceed, max_width.

    Args:
        text: Text to wrap.
        family: Font family name.
        size: Font size.
        max_width: Maximum line width.

    Returns:
        A list of wrapped lines.
    """
    if max_width <= 0:
        return [text]

    words = text.split()
    if not words:
        return [text]

    lines = []
    current_line = ""

    for word in words:
        if not current_line:
            # The current line is empty, so place the word directly.
            current_line = word
        else:
            # Try appending the word to the current line.
            candidate = current_line + " " + word
            if estimate_text_width(candidate, family, size) <= max_width:
                current_line = candidate
            else:
                # The word does not fit, so start a new line.
                lines.append(current_line)
                current_line = word

    # Add the last line.
    if current_line:
        lines.append(current_line)

    return lines if lines else [text]


# =============================================================================
#                           PDF content stream handling
# =============================================================================

def merge_contents_to_single_stream(page_obj: pikepdf.Page) -> str:
    """
    Merge the page content streams into a single string.

    The /Contents entry may be either a single Stream or an array of Streams.
    This helper normalizes both cases.

    Args:
        page_obj: The page object.

    Returns:
        The merged content stream as a string.
    """
    contents = safe_get_object(page_obj['/Contents'])
    if isinstance(contents, Array):
        parts = []
        for item in contents:
            stream = safe_get_object(item)
            parts.append(stream.read_bytes().decode('latin-1'))
        return ''.join(parts)
    elif isinstance(contents, Stream):
        return contents.read_bytes().decode('latin-1')
    else:
        raise ValueError(f"Unknown /Contents type: {type(contents)}")


def analyze_page_fonts(content: str) -> dict:
    """
    Analyze all fonts used in the page content stream and count their usage.

    This scans Tf operators in the content stream and records the frequency
    of each font resource name.

    Args:
        content: The page content stream string.

    Returns:
        A dictionary containing:
        - font_frequency: mapping from font name to usage count,
          e.g. {'/F136': 45, '/F1': 12}
        - most_used_font: the most frequently used font resource name
        - total_font_uses: total number of font uses found
    """
    font_frequency = {}

    # Match Tf operators and capture font names such as /F136, /F1, /T1_0.
    # Format: /FontName Size Tf
    tf_pattern = r"(/[A-Za-z][A-Za-z0-9_]*)\s+(-?\d+\.?\d*)\s+Tf"
    for match in re.finditer(tf_pattern, content):
        font_name = match.group(1)
        font_frequency[font_name] = font_frequency.get(font_name, 0) + 1

    # Find the most frequently used font.
    most_used_font = None
    if font_frequency:
        most_used_font = max(font_frequency.items(), key=lambda item: item[1])[0]

    return {
        'font_frequency': font_frequency,
        'most_used_font': most_used_font,
        'total_font_uses': sum(font_frequency.values()),
    }


def make_text_content(x: float, y: float, font: str, size: float,
                      lines: List[str] = None, raw_hex: bool = False,
                      invisible: bool = True, target_width: float = None,
                      font_family: str = "Helvetica",
                      line_spacing: float = 0.0,
                      selectable_line_count: int = -1,
                      tm_scale: float = 1.0) -> str:
    """
    Generate PDF content for drawing text.

    Builds BT...ET text blocks and supports both multi-line output and
    width-aligned placement.

    Justification strategy:
    - Split each line into a prefix and the last word.
    - Render the prefix from the starting x coordinate.
    - Place the last word so its right edge aligns exactly with
      x + target_width.
    - This makes the selectable width of each line consistent.

    Notes on Tm matrix scaling:
    - Effective displayed font size in PDF = Tf size × Tm scale factor.
    - Many PDFs use patterns like `/F3 1 Tf` plus
      `8.9664 0 0 8.9664 x y Tm`.
    - To make the injected text's selection bounding box consistent with
      the surrounding text, this function can use the same Tm scaling mode.
    - When tm_scale is provided, it uses `size/tm_scale Tf` together with
      `tm_scale 0 0 tm_scale x y Tm`.

    Args:
        x: Starting x coordinate.
        y: Starting y coordinate.
        font: Font resource name.
        size: Target displayed font size.
        lines: List of text lines.
        raw_hex: Whether to use hex encoding.
        invisible: Whether to use invisible rendering mode (3 Tr).
        target_width: Target width used for justification.
        font_family: Font family name used for width estimation.
        line_spacing: Line spacing in points. Positive values move downward.
        selectable_line_count: Number of lines selectable by the cursor.
                               -1 or any negative value: all lines selectable.
                               0: all lines unselectable, marked as Artifact.
                               n (positive): first n lines selectable, the rest
                               marked as Artifact.
        tm_scale: Tm matrix scale factor used to match the original text
                  selection box height. Default is 1.0.

    Returns:
        A PDF content stream string.
    """
    out = ["q\n"]

    lines_list = lines or []

    # Font size used in Tf: displayed size divided by tm_scale, so that
    # base_font_size × tm_scale = target displayed size.
    if tm_scale > 0:
        base_font_size = size / tm_scale
    else:
        base_font_size = size
        tm_scale = 1.0

    for i, ln in enumerate(lines_list):
        words = ln.split() if ln else []

        # Compute the y coordinate for the current line.
        current_y = y - (i * line_spacing)

        # Justification is only used when target_width is given and the line
        # has more than one word.
        should_justify = target_width and target_width > 0 and len(words) > 1

        # Decide whether the current line should be marked as Artifact.
        if selectable_line_count < 0:
            is_artifact = False
        else:
            is_artifact = i >= selectable_line_count

        # Start Artifact marking if needed.
        if is_artifact:
            out.append("/Artifact BMC\n")

        # Start the text block.
        out.append("BT\n")
        out.append(f"{font} {base_font_size:.6f} Tf\n")

        # Use Text Rendering Mode 3 for invisible text.
        if invisible:
            out.append("3 Tr\n")

        if should_justify and not raw_hex:
            # Justified mode: place the last word so its right edge aligns
            # with x + target_width.
            last_word = words[-1]
            prefix = ' '.join(words[:-1])

            # Estimate the width of the last word using the displayed size.
            last_word_width = estimate_text_width(last_word, font_family, size)

            # Compute the starting x coordinate for the last word.
            last_word_x = x + target_width - last_word_width

            # Render the prefix from the initial x position.
            out.append(f"{tm_scale:.6f} 0 0 {tm_scale:.6f} {x} {current_y:.4f} Tm\n")
            out.append(f"({_escape_pdf_literal(prefix)}) Tj\n")

            # Render the last word so it aligns to the right boundary.
            out.append(f"{tm_scale:.6f} 0 0 {tm_scale:.6f} {last_word_x:.4f} {current_y:.4f} Tm\n")
            out.append(f"({_escape_pdf_literal(last_word)}) Tj\n")
        else:
            # No justification needed, or justification cannot be applied.
            out.append(f"{tm_scale:.6f} 0 0 {tm_scale:.6f} {x} {current_y:.4f} Tm\n")
            if raw_hex:
                out.append(f"<{ln}> Tj\n")
            else:
                out.append(f"({_escape_pdf_literal(ln)}) Tj\n")

        # End the text block.
        out.append("ET\n")

        # End Artifact marking if needed.
        if is_artifact:
            out.append("EMC\n")

    out.append("Q\n")

    return "".join(out)


# =============================================================================
#                           Tj/TJ analysis and width estimation
# =============================================================================

def estimate_tj_width(tj_content: str, font_size: float, font_family: str = "Helvetica") -> float:
    """
    Estimate the text width of a Tj or TJ operation.

    This parses the operation content and computes the approximate total width.
    Numeric values inside a TJ array represent spacing adjustments.

    Args:
        tj_content: Full Tj or TJ operation content.
        font_size: Current font size.
        font_family: Font family name.

    Returns:
        Estimated text width.
    """
    # Handle Tj: (text) Tj
    if tj_content.strip().startswith('('):
        text_match = re.search(r'\(([^)]*)\)\s*Tj', tj_content)
        if text_match:
            text = text_match.group(1)
            text = text.replace('\\(', '(').replace('\\)', ')')
            return estimate_text_width(text, font_family, font_size)

    # Handle TJ: [text1 spacing1 text2 ...] TJ
    if tj_content.strip().startswith('['):
        tj_match = re.search(r'\[(.*?)\]\s*TJ', tj_content, re.DOTALL)
        if tj_match:
            array_content = tj_match.group(1)
            total_width = 0.0
            elements = re.findall(r'(\([^)]*\)|-?\d+\.?\d*)', array_content)
            for elem in elements:
                if elem.startswith('('):
                    text = elem[1:-1].replace('\\(', '(').replace('\\)', ')')
                    total_width += estimate_text_width(text, font_family, font_size)
                else:
                    try:
                        # Spacing adjustment units are in thousandths of font size.
                        spacing = float(elem)
                        total_width += spacing * font_size / 1000.0
                    except ValueError:
                        pass

            return total_width

    return 0.0

# Precompiled PDF operator regexes at module scope to avoid recompilation.
_NUMBER_PATTERN = r"-?\d+\.?\d*"
# Font resource name pattern: supports /F1, /F155, /T1_0, /T1_2, /TT0, etc.
_FONT_NAME_PATTERN = r"/[A-Za-z][A-Za-z0-9_]*"
_PDF_TOKEN_PATTERN = re.compile(
    rf"""
    # -------- Tm: set text matrix (absolute positioning) --------
    (?P<Tm>
        {_NUMBER_PATTERN}\s+{_NUMBER_PATTERN}\s+{_NUMBER_PATTERN}\s+
        {_NUMBER_PATTERN}\s+{_NUMBER_PATTERN}\s+{_NUMBER_PATTERN}\s+Tm
    )
    |
    # -------- TD: relative move + set leading --------
    (?P<TD>
        {_NUMBER_PATTERN}\s+{_NUMBER_PATTERN}\s+TD
    )
    |
    # -------- Td: relative move --------
    (?P<Td>
        {_NUMBER_PATTERN}\s+{_NUMBER_PATTERN}\s+Td
    )
    |
    # -------- Tf: set font and size --------
    (?P<Tf>
        {_FONT_NAME_PATTERN}\s+{_NUMBER_PATTERN}\s+Tf
    )
    |
    # -------- TJ: array-form text drawing --------
    (?P<TJ>
        \[.*?\]\s*TJ
    )
    |
    # -------- Tj: string-form text drawing --------
    (?P<Tj>
        \((?:\\.|[^\\)])*\)\s*Tj
    )
    """,
    re.VERBOSE | re.DOTALL,
)


def collect_tj_widths_and_positions(content: str, page_height: float = 792.0) -> List[Tuple[re.Match, float, float, float, float, float, float]]:
    """
    Scan a PDF page content stream and simulate the PDF text state machine.

    Supports Tm / Td / TD / Tf / Tj / TJ, and records width and position
    whenever text is drawn by Tj or TJ.

    Note:
        Effective font size = base font size from Tf × Tm scale.
        Many PDFs use patterns such as `/F3 1 Tf` with
        `8.9664 0 0 8.9664 x y Tm`, so the actual displayed size is
        1 × 8.9664 = 8.9664.

    For two-column papers, moving from the first column to the second via Td
    may produce a y coordinate outside the page bounds. This function detects
    such cases and normalizes y back into the page range.

    Args:
        content: Page content stream string.
        page_height: Page height used to normalize cross-column jumps.

    Returns:
        A list of tuples:
        (match, width, x, y, effective_font_size, base_font_size, tm_scale)
    """
    results: List[Tuple[re.Match, float, float, float, float, float, float]] = []

    # PDF text state.
    current_x = 0.0
    current_y = 0.0
    base_font_size = 12.0
    tm_scale = 1.0

    # If Td causes a large y jump together with a notable x shift, it may
    # indicate a jump from one column to another.
    COLUMN_JUMP_THRESHOLD = 300.0

    # Scan tokens in order.
    for m in _PDF_TOKEN_PATTERN.finditer(content):
        token = m.group(0)
        op = m.lastgroup

        if op == "Tm":
            # a b c d e f Tm
            # a and d are scale factors, e and f are coordinates.
            parts = token.split(maxsplit=6)
            a, d = float(parts[0]), float(parts[3])
            current_x, current_y = float(parts[4]), float(parts[5])
            tm_scale = max(abs(a), abs(d))

        elif op == "Td" or op == "TD":
            # dx dy Td/TD -> relative move
            parts = token.split(maxsplit=2)
            dx = float(parts[0])
            dy = float(parts[1])
            current_x += dx
            current_y += dy

            # Detect likely cross-column jumps.
            if dy > COLUMN_JUMP_THRESHOLD and dx > 50:
                # Normalize y back into the page range.
                if current_y > page_height:
                    overflow = current_y - page_height
                    current_y = page_height - overflow
                    if current_y < 0:
                        # Fallback: place it near the top of the page.
                        current_y = page_height * 0.9

        elif op == "Tf":
            # /F155 10.96 Tf -> set base font size.
            base_font_size = float(token.split(maxsplit=2)[1])

        elif op in ("Tj", "TJ"):
            # Effective size = base size × Tm scale.
            effective_font_size = base_font_size * tm_scale
            width = estimate_tj_width(token, effective_font_size)
            if width > 0:
                results.append((m, width, current_x, current_y, effective_font_size, base_font_size, tm_scale))

    return results


# =============================================================================
#                           Main injection function
# =============================================================================

def insert_text_after_tj(
    pdf_obj: pikepdf.Pdf,
    page_obj: pikepdf.Page,
    text: str = None,
    *,
    prompt_pool: Optional[List[str]] = None,
    target_width: float = 200.0,
    target_x1: float = 50.0,
    target_x2: float = 300.0,
    target_font_size: float = 8.25,
    width_tolerance: float = 0.1,
    position_tolerance: float = 0.1,
    insert_count: int = 1,
    use_random_count: bool = False,
    insertion_probability: float = 1.0,
    line_spacing: float = 0.0,
    selectable_line_count: int = 0,
) -> dict:
    """
    Inject text after matching Tj/TJ operations on a page.

    Workflow:
    1. Parse the page content stream and collect width/position information
       for all Tj/TJ operations.
    2. Only keep text runs that satisfy both conditions:
       - width is close to target_width
       - x position is close to target_x1 or target_x2
         (supports two-column papers)
    3. For each matching Tj/TJ, inject text after it:
       - use the most frequently used font on the page
       - randomly choose injected text from prompt_pool
       - use target_font_size for injected text
       - keep the same starting position as the original text
    4. Also inject once near the beginning of the page.
    5. Write the updated content stream back to the page.

    Args:
        pdf_obj: PDF document object.
        page_obj: Page object.
        text: Fixed text to inject. Either this or prompt_pool must be provided.
        prompt_pool: Pool of defensive prompts. If provided, takes precedence.
        target_width: Target text width used for matching.
        target_x1: Target x coordinate for the first column.
        target_x2: Target x coordinate for the second column.
        target_font_size: Font size used for injected text.
        width_tolerance: Relative width tolerance. Default is 0.1.
        position_tolerance: Relative x-position tolerance. Default is 0.1.
        insert_count: Maximum insertion count per location.
        use_random_count: Whether to choose insertion count randomly from
                          1 to insert_count.
        insertion_probability: Probability of actually performing an insertion
                               after a location and text have already been chosen.
        line_spacing: Line spacing in points. Default 0 means overlap.
        selectable_line_count: Number of lines that remain selectable.
                               Negative: all lines selectable.
                               0: all lines marked as Artifact.
                               Positive n: first n lines selectable.

    Returns:
        A statistics dictionary containing:
        - attempted_tjs
        - performed_insertions
        - total_insertions
        - target_font_size
        - target_width / target_x1 / target_x2
    """
    # Validate arguments: at least one of text or prompt_pool is required.
    if text is None and prompt_pool is None:
        raise ValueError("Either text or prompt_pool must be provided.")

    # If prompt_pool is given, use it; otherwise wrap text in a list.
    effective_pool = prompt_pool if prompt_pool is not None else [text]
    existing_content = merge_contents_to_single_stream(page_obj)

    # Analyze page fonts and pick the most frequently used one.
    font_analysis = analyze_page_fonts(existing_content)
    most_used_font = font_analysis['most_used_font']

    # If the page has no font usage at all, fall back to a default font.
    if most_used_font is None:
        most_used_font = ensure_font_from_pool(pdf_obj, page_obj, "Helvetica")

    # Get page bounds so coordinates can be validated.
    page_x_min, page_y_min, page_x_max, page_y_max = get_page_dimensions(page_obj)
    page_height = page_y_max - page_y_min

    # Collect all Tj/TJ widths and positions.
    tj_info_list = collect_tj_widths_and_positions(existing_content, page_height)

    if not tj_info_list:
        return {'attempted_tjs': 0, 'performed_insertions': 0, 'skipped_insertions': 0,
                'total_insertions': 0, 'target_font_size': target_font_size,
                'target_width': target_width, 'target_x1': target_x1, 'target_x2': target_x2,
                'insertion_probability': insertion_probability,
                'used_font': most_used_font, 'font_frequency': font_analysis['font_frequency'],
                'median_tm_scale': 1.0}

    # Width filter range: target_width ± tolerance.
    width_min = target_width * (1 - width_tolerance)
    width_max = target_width * (1 + width_tolerance)

    # Position filter ranges for both columns.
    x1_min = target_x1 * (1 - position_tolerance)
    x1_max = target_x1 * (1 + position_tolerance)
    x2_min = target_x2 * (1 - position_tolerance)
    x2_max = target_x2 * (1 + position_tolerance)

    content = existing_content
    total_inserted_texts = 0
    performed_insertions = 0
    skipped_insertions = 0  # Insertions skipped due to probability.

    # Filter candidates by width, x position, and page bounds.
    def is_match(w, x, y):
        width_ok = width_min <= w <= width_max
        x_in_col1 = x1_min <= x <= x1_max
        x_in_col2 = x2_min <= x <= x2_max
        y_in_page = page_y_min <= y <= page_y_max
        x_in_page = page_x_min <= x <= page_x_max
        return width_ok and (x_in_col1 or x_in_col2) and y_in_page and x_in_page

    filtered_info = [(m, w, x, y, efs, bfs, tms) for m, w, x, y, efs, bfs, tms in tj_info_list if is_match(w, x, y)]
    filtered_info.sort(key=lambda item: item[0].start(), reverse=True)

    # Save the earliest matching text position for the extra page-start insertion.
    first_tj_info = min(filtered_info, key=lambda item: item[0].start()) if filtered_info else None

    # Use the median tm_scale among matches as the default insertion scale.
    if filtered_info:
        all_tm_scales = [tms for _, _, _, _, _, _, tms in filtered_info]
        all_tm_scales.sort()
        n = len(all_tm_scales)
        median_tm_scale = all_tm_scales[n // 2] if n % 2 else (all_tm_scales[n // 2 - 1] + all_tm_scales[n // 2]) / 2.0
    else:
        median_tm_scale = 1.0

    def find_et_after_position(content_str: str, start_pos: int) -> int:
        """
        Find the end position of the nearest ET operator after start_pos.

        Args:
            content_str: Content stream string.
            start_pos: Search start position.

        Returns:
            The position immediately after ET, including trailing whitespace.
        """
        # Search for ET as a standalone operator.
        et_pattern = re.compile(r'[\s]ET(?=[\s]|$)')
        match = et_pattern.search(content_str, start_pos)
        if match:
            return match.end()
        return start_pos

    def do_insertion(pos: int, tj_x: float, tj_y: float, tj_width: float, tj_tm_scale: float = None) -> int:
        """
        Insert an independent text block after the ET of the containing text block.

        Args:
            pos: Insertion anchor position.
            tj_x: Original text x coordinate.
            tj_y: Original text y coordinate.
            tj_width: Original text width.
            tj_tm_scale: Tm scale of the original text, used to align the
                         selection box height.

        Returns:
            Number of inserted lines.
        """
        nonlocal content, total_inserted_texts

        num_insertions = random.randint(1, insert_count) if use_random_count else 1

        # Use the provided tm_scale when available; otherwise use the median.
        use_tm_scale = tj_tm_scale if tj_tm_scale is not None else median_tm_scale

        # Insert after the ET of the current text block so the injected content
        # becomes an independent BT...ET block.
        et_end_pos = find_et_after_position(content, pos)
        current_pos = et_end_pos
        lines_inserted = 0

        for _ in range(num_insertions):
            # Randomly choose the injected text from the effective pool.
            chosen_text = random.choice(effective_pool)

            # Reuse the most frequently used font on the page.
            font_res = most_used_font

            # Wrap to target_width using the target font size.
            lines = wrap_text_to_width(chosen_text, "Helvetica", target_font_size, target_width)

            # Generate and insert the content as an independent BT...ET block.
            text_content = make_text_content(
                x=tj_x, y=tj_y,
                font=font_res, size=target_font_size,
                lines=lines,
                raw_hex=False,
                target_width=target_width,
                font_family="Helvetica",
                line_spacing=line_spacing,
                selectable_line_count=selectable_line_count,
                tm_scale=use_tm_scale
            )

            prefix = "\n" if current_pos > 0 and content[current_pos - 1] not in " \t\r\n" else ""
            suffix = "\n" if current_pos < len(content) and content[current_pos] not in " \t\r\n" else ""
            insertion = prefix + text_content + suffix
            content = content[:current_pos] + insertion + content[current_pos:]
            current_pos += len(insertion)

            lines_inserted += len(lines)
            total_inserted_texts += len(lines)

        return lines_inserted

    # Inject for each matching Tj/TJ from back to front.
    for match, tj_width, tj_x, tj_y, _, _, tj_tm_scale in filtered_info:
        if random.random() < insertion_probability:
            do_insertion(match.end(), tj_x, tj_y, tj_width, tj_tm_scale)
            performed_insertions += 1
        else:
            skipped_insertions += 1

    # Add one extra insertion near the beginning of the page.
    if first_tj_info:
        _, first_width, first_x, first_y, _, _, first_tm_scale = first_tj_info
        if random.random() < insertion_probability:
            bt_match = re.search(r"[\n ]BT[\n ]", content)
            first_bt_pos = bt_match.start() if bt_match else 0
            do_insertion(first_bt_pos, first_x, first_y, first_width, first_tm_scale)
            performed_insertions += 1
        else:
            skipped_insertions += 1

    # Write the updated content back to the page.
    new_stream = Stream(pdf_obj, content.encode('latin-1'), compress=True)
    page_obj['/Contents'] = new_stream

    return {
        'attempted_tjs': len(filtered_info),
        'performed_insertions': performed_insertions,
        'skipped_insertions': skipped_insertions,
        'total_insertions': total_inserted_texts,
        'target_font_size': target_font_size,
        'target_width': target_width,
        'target_x1': target_x1,
        'target_x2': target_x2,
        'insertion_probability': insertion_probability,
        'width_range': (width_min, width_max),
        'x1_range': (x1_min, x1_max),
        'x2_range': (x2_min, x2_max),
        'used_font': most_used_font,
        'font_frequency': font_analysis['font_frequency'],
        'median_tm_scale': median_tm_scale,
    }


import os
import sys
import json
import time
import logging
from datetime import datetime

# =============================================================================
#                           Logging configuration
# =============================================================================

def setup_logger(log_file_path):
    """Configure the logger to output to both the console and a file."""
    logger = logging.getLogger("PDFProcessor")
    logger.setLevel(logging.DEBUG)
    
    # Clear existing handlers to avoid duplicate additions.
    logger.handlers.clear()
    
    # Create the log format.
    formatter = logging.Formatter(
        fmt='%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Console handler (use stdout instead of the default stderr to avoid red output in PyCharm).
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # File handler.
    file_handler = logging.FileHandler(log_file_path, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    return logger

# =============================================================================
#                           Main program entry
# =============================================================================

if __name__ == "__main__":

    # Define all venues.

    venues = [
        "NeurIPS", "ICLR", "ICML", "Nature", "Nature_Biotechnology",
        "NDSS", "USENIX_Security", "CCS", "SP",
        "Advanced_Materials", "Psychological_Review", "ITS"
    ]

    # Define the defense strategy: explicit or implicit.
    defense_strategy = "explicit"

    # Define paths.
    input_base_dir = "./data/dataset_raw"
    output_base_dir = "./data/dataset_{}_layer_cake".format(defense_strategy)
    stats_output_path = os.path.join(output_base_dir, "processing_stats_{}.json".format(defense_strategy))
    
    # Generate a timestamped log filename.
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file_path = os.path.join(output_base_dir, f"processing_log_{defense_strategy}_{timestamp}.log")
    
    # Ensure the output directory exists.
    os.makedirs(output_base_dir, exist_ok=True)
    
    # Configure logging.
    logger = setup_logger(log_file_path)
    
    logger.info("=" * 60)
    logger.info("PDF Processing Started")
    logger.info("=" * 60)
    logger.info(f"Input directory: {input_base_dir}")
    logger.info(f"Output directory: {output_base_dir}")
    logger.info(f"Stats output: {stats_output_path}")
    logger.info(f"Log file: {log_file_path}")
    logger.info(f"Venues to process: {', '.join(venues)}")
    
    # Load the defensive prompt pool from the JSON file.
    prompt_pool = load_defensive_prompt_pool("./configuration/{}_defensive_prompt_pool.json".format(defense_strategy))
    logger.info(f"Loaded {len(prompt_pool)} defensive prompts from pool")
    
    # Statistics list.
    all_stats = []
    
    # Record the overall start time.
    total_start_time = time.time()
    
    # Process each PDF for each venue.
    for venue in venues:
        logger.info("")
        logger.info("=" * 60)
        logger.info(f"Processing venue: {venue}")
        logger.info("=" * 60)
        
        # Get the configuration for this venue.
        config = get_venue_config(venue)
        logger.debug(f"Venue config - target_width: {config.target_width}, target_font_size: {config.target_font_size}, insertion_probability: {config.insertion_probability}")
        
        # Create the output directory if it does not exist.
        output_venue_dir = os.path.join(output_base_dir, venue)
        os.makedirs(output_venue_dir, exist_ok=True)
        logger.debug(f"Output directory created/verified: {output_venue_dir}")
        
        # Process the 10 PDFs under this venue (index 0-9).
        for index in range(10):
            pdf_filename = f"{venue}_{index}.pdf"
            input_pdf = os.path.join(input_base_dir, venue, pdf_filename)
            output_pdf = os.path.join(output_venue_dir, pdf_filename)
            
            logger.info(f"Processing: {pdf_filename}")
            
            # Check whether the input file exists.
            if not os.path.exists(input_pdf):
                logger.warning(f"Input file not found: {input_pdf}")
                continue
            
            # Get the file size before processing (MB).
            size_before_bytes = os.path.getsize(input_pdf)
            size_before_mb = size_before_bytes / (1024 * 1024)
            logger.debug(f"Input file size: {size_before_mb:.4f} MB")
            
            # Record the start time.
            start_time = time.time()
            
            try:
                # Open the PDF.
                pdf = pikepdf.open(input_pdf)
                logger.debug(f"PDF opened successfully, pages: {len(pdf.pages)}")
                
                total_insertions = 0
                total_performed = 0
                total_skipped = 0
                
                # Process each page.
                for page_number, page in enumerate(pdf.pages, start=1):
                    stats = insert_text_after_tj(
                        pdf, page,
                        prompt_pool=prompt_pool,
                        target_width=config.target_width,
                        target_x1=config.target_x1,
                        target_x2=config.target_x2,
                        target_font_size=config.target_font_size,
                        width_tolerance=config.width_tolerance,
                        position_tolerance=config.position_tolerance,
                        insert_count=config.insert_count,
                        use_random_count=config.use_random_count,
                        insertion_probability=1.0,
                    )
                    total_performed += stats['performed_insertions']
                    total_skipped += stats['skipped_insertions']
                    total_insertions += stats['total_insertions']
                    logger.debug(f"  Page {page_number}: performed={stats['performed_insertions']}, skipped={stats['skipped_insertions']}, total={stats['total_insertions']}")
                
                # Save the processed PDF.
                pdf.save(output_pdf)
                pdf.close()
                
                # Record the end time.
                end_time = time.time()
                processing_time = end_time - start_time
                
                # Get the file size after processing (MB).
                size_after_bytes = os.path.getsize(output_pdf)
                size_after_mb = size_after_bytes / (1024 * 1024)
                
                # Record statistics.
                pdf_stats = {
                    "venue": venue,
                    "index": index,
                    "filename": pdf_filename,
                    "size_before_mb": round(size_before_mb, 4),
                    "size_after_mb": round(size_after_mb, 4),
                    "size_increase_mb": round(size_after_mb - size_before_mb, 4),
                    "processing_time_seconds": round(processing_time, 2),
                    "total_performed": total_performed,
                    "total_skipped": total_skipped,
                    "total_insertions": total_insertions,
                    "insertion_probability": config.insertion_probability,
                    "status": "success"
                }
                all_stats.append(pdf_stats)
                
                size_diff = size_after_mb - size_before_mb
                size_diff_str = f"+{size_diff:.4f}" if size_diff >= 0 else f"{size_diff:.4f}"
                logger.info(f"  Size: {size_before_mb:.4f} MB -> {size_after_mb:.4f} MB ({size_diff_str} MB)")
                logger.info(f"  Time: {processing_time:.2f}s | Insertions: {total_performed} performed, {total_skipped} skipped, {total_insertions} total lines")
                logger.info(f"  [SUCCESS] Saved to: {output_pdf}")
                
            except Exception as e:
                # Record the end time, even if processing fails.
                end_time = time.time()
                processing_time = end_time - start_time
                
                # Record failure statistics.
                pdf_stats = {
                    "venue": venue,
                    "index": index,
                    "filename": pdf_filename,
                    "size_before_mb": round(size_before_mb, 4),
                    "size_after_mb": None,
                    "size_increase_mb": None,
                    "processing_time_seconds": round(processing_time, 2),
                    "total_performed": None,
                    "total_skipped": None,
                    "total_insertions": None,
                    "insertion_probability": config.insertion_probability,
                    "status": "failed",
                    "error": str(e)
                }
                all_stats.append(pdf_stats)
                
                logger.error(f"  [FAILED] Error processing {pdf_filename}: {e}")
    
    # Save all statistics to a JSON file.
    with open(stats_output_path, 'w', encoding='utf-8') as f:
        json.dump(all_stats, f, ensure_ascii=False, indent=2)
    
    # Calculate the total processing time.
    total_end_time = time.time()
    total_processing_time = total_end_time - total_start_time
    
    # ==========================================================================
    # Statistical analysis by venue.
    # ==========================================================================
    venue_summary = {}
    for venue in venues:
        venue_stats = [s for s in all_stats if s['venue'] == venue and s['status'] == 'success']
        if not venue_stats:
            venue_summary[venue] = {
                "pdf_count": 0,
                "successful_count": 0,
                "failed_count": len([s for s in all_stats if s['venue'] == venue]),
                "avg_insertions": None,
                "avg_lines": None,
                "avg_skipped": None,
                "avg_size_increase_mb": None,
                "avg_processing_time_seconds": None,
                "total_insertions": None,
                "total_lines": None,
                "total_skipped": None,
                "total_size_increase_mb": None,
                "total_processing_time_seconds": None,
            }
            continue
        
        # Calculate statistics for this venue.
        pdf_count = len(venue_stats)
        total_performed = sum(s['total_performed'] for s in venue_stats)
        total_lines = sum(s['total_insertions'] for s in venue_stats)
        total_skipped = sum(s['total_skipped'] for s in venue_stats)
        total_size_increase = sum(s['size_increase_mb'] for s in venue_stats)
        total_time = sum(s['processing_time_seconds'] for s in venue_stats)
        
        venue_summary[venue] = {
            "pdf_count": pdf_count,
            "successful_count": pdf_count,
            "failed_count": len([s for s in all_stats if s['venue'] == venue and s['status'] == 'failed']),
            "avg_insertions": round(total_performed / pdf_count, 2),
            "avg_lines": round(total_lines / pdf_count, 2),
            "avg_skipped": round(total_skipped / pdf_count, 2),
            "avg_size_increase_mb": round(total_size_increase / pdf_count, 4),
            "avg_processing_time_seconds": round(total_time / pdf_count, 2),
            "total_insertions": total_performed,
            "total_lines": total_lines,
            "total_skipped": total_skipped,
            "total_size_increase_mb": round(total_size_increase, 4),
            "total_processing_time_seconds": round(total_time, 2),
        }
    
    # Calculate the global summary.
    successful_count = sum(1 for s in all_stats if s['status'] == 'success')
    failed_count = sum(1 for s in all_stats if s['status'] == 'failed')
    total_size_before = sum(s['size_before_mb'] for s in all_stats)
    total_size_after = sum(s['size_after_mb'] for s in all_stats if s['size_after_mb'] is not None)
    total_insertions_all = sum(s['total_performed'] for s in all_stats if s['total_performed'] is not None)
    total_lines_all = sum(s['total_insertions'] for s in all_stats if s['total_insertions'] is not None)
    total_skipped_all = sum(s['total_skipped'] for s in all_stats if s['total_skipped'] is not None)
    
    # Build the complete summary report.
    summary_report = {
        "global_summary": {
            "total_pdfs_processed": len(all_stats),
            "successful_count": successful_count,
            "failed_count": failed_count,
            "total_processing_time_seconds": round(total_processing_time, 2),
            "total_size_before_mb": round(total_size_before, 4),
            "total_size_after_mb": round(total_size_after, 4),
            "total_size_change_mb": round(total_size_after - total_size_before, 4),
            "total_insertions": total_insertions_all,
            "total_lines": total_lines_all,
            "total_skipped": total_skipped_all,
            "avg_insertions_per_pdf": round(total_insertions_all / successful_count, 2) if successful_count > 0 else None,
            "avg_lines_per_pdf": round(total_lines_all / successful_count, 2) if successful_count > 0 else None,
            "avg_size_increase_per_pdf_mb": round((total_size_after - total_size_before) / successful_count, 4) if successful_count > 0 else None,
            "avg_processing_time_per_pdf_seconds": round(total_processing_time / successful_count, 2) if successful_count > 0 else None,
        },
        "venue_summary": venue_summary,
    }
    
    # Save the venue summary report to a JSON file.
    venue_summary_path = os.path.join(output_base_dir, "venue_summary_{}.json".format(defense_strategy))
    with open(venue_summary_path, 'w', encoding='utf-8') as f:
        json.dump(summary_report, f, ensure_ascii=False, indent=2)
    
    # Output logs.
    logger.info("")
    logger.info("=" * 60)
    logger.info("Processing Complete!")
    logger.info("=" * 60)
    logger.info(f"Total PDFs processed: {len(all_stats)}")
    logger.info(f"Successful: {successful_count}")
    logger.info(f"Failed: {failed_count}")
    logger.info(f"Total processing time: {total_processing_time:.2f}s ({total_processing_time/60:.2f} min)")
    logger.info(f"Total size before: {total_size_before:.4f} MB")
    logger.info(f"Total size after: {total_size_after:.4f} MB")
    total_size_diff = total_size_after - total_size_before
    total_size_diff_str = f"+{total_size_diff:.4f}" if total_size_diff >= 0 else f"{total_size_diff:.4f}"
    logger.info(f"Total size change: {total_size_diff_str} MB")
    logger.info(f"Total insertions performed: {total_insertions_all}")
    
    # Output the statistical summary for each venue.
    logger.info("")
    logger.info("=" * 60)
    logger.info("Per-Venue Summary")
    logger.info("=" * 60)
    logger.info(f"{'Venue':<25} {'PDFs':>5} {'AvgIns':>8} {'AvgLines':>10} {'AvgSize(MB)':>12} {'AvgTime(s)':>11}")
    logger.info("-" * 75)
    for venue, stats in venue_summary.items():
        if stats['avg_insertions'] is not None:
            logger.info(f"{venue:<25} {stats['pdf_count']:>5} {stats['avg_insertions']:>8.2f} {stats['avg_lines']:>10.2f} {stats['avg_size_increase_mb']:>12.4f} {stats['avg_processing_time_seconds']:>11.2f}")
        else:
            logger.info(f"{venue:<25} {stats['pdf_count']:>5} {'N/A':>8} {'N/A':>10} {'N/A':>12} {'N/A':>11}")
    
    # Output the average results across all venues.
    logger.info("-" * 75)
    global_avg = summary_report['global_summary']
    if global_avg['avg_insertions_per_pdf'] is not None:
        logger.info(f"{'ALL (Average)':<25} {successful_count:>5} {global_avg['avg_insertions_per_pdf']:>8.2f} {global_avg['avg_lines_per_pdf']:>10.2f} {global_avg['avg_size_increase_per_pdf_mb']:>12.4f} {global_avg['avg_processing_time_per_pdf_seconds']:>11.2f}")
    else:
        logger.info(f"{'ALL (Average)':<25} {successful_count:>5} {'N/A':>8} {'N/A':>10} {'N/A':>12} {'N/A':>11}")
    
    logger.info("")
    logger.info(f"Statistics saved to: {stats_output_path}")
    logger.info(f"Venue summary saved to: {venue_summary_path}")
    logger.info(f"Log saved to: {log_file_path}")
