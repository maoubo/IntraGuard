"""
Implementation:
- Text is invisible (transparent)
- Wrap with /Span << /ActualText ... >> BDC ... EMC so it is parsed as a whole
- Actually draw only a single space (very small selection area); the full text is provided by ActualText
- Automatically detect the most frequently used font on the page

Dependency: pip install pikepdf
"""

from __future__ import annotations
from dataclasses import dataclass
from collections import Counter
import random
import re
import pikepdf
from pikepdf import Name, Dictionary, Stream, Array


def to_pdf_hex_utf16be(text: str) -> str:
    """Generate UTF-16BE with BOM in hexadecimal form: <FEFF....>"""
    return (b"\xFE\xFF" + text.encode("utf-16-be")).hex().upper()


def escape_pdf_literal(s: str) -> str:
    """Escape a literal string in the form of ( ... )"""
    return (s.replace("\\", "\\\\")
             .replace("(", "\\(").replace(")", "\\)")
             .replace("\r", "\\r").replace("\n", "\\n"))


def get_most_frequent_font(page: pikepdf.Page) -> str | None:
    """Analyze the page content stream and return the most frequently used font resource name (for example, "/F148")"""
    contents = page.get("/Contents")
    if contents is None:
        return None

    # Collect content stream data
    streams = [contents] if isinstance(contents, pikepdf.Stream) else contents
    raw = b""
    for s in streams:
        if isinstance(s, pikepdf.Stream):
            try:
                raw += s.read_bytes()
            except Exception:
                continue
    if not raw:
        return None

    # Match patterns like /Fxxx number Tf
    matches = re.findall(rb'(/F\d+)\s+[\d.]+\s+Tf', raw)
    if not matches:
        return None

    most_common = Counter(matches).most_common(1)[0][0]
    return most_common.decode("ascii")


@dataclass
class InjectSpec:
    count: int = 5                     # Number of insertions
    font_size: float = 10.0
    font_key: str | None = None        # None = automatically detect the most frequent font on the page
    gs_key: str = "/GS_AI_0"
    use_hex: bool = True               # Use UTF-16BE hex for ActualText (recommended: True)


def get_page_dimensions(page: pikepdf.Page) -> tuple[float, float]:
    """Get page width and height (pt), trying MediaBox / CropBox in order, defaulting to A4"""
    for key in ("/MediaBox", "/CropBox"):
        box = page.get(key)
        if box is not None:
            coords = [float(v) for v in box]
            return coords[2] - coords[0], coords[3] - coords[1]
    return 612.0, 792.0  # Default A4


def build_invisible_text_stream(
    text: str, spec: InjectSpec, font_key: str, x: float, y: float,
) -> bytes:
    """
    Generate the content stream:
      q BT gs Tf Tm BDC ( )Tj EMC ET Q
    ActualText contains the full text, while only a single space is actually drawn.
    """
    if spec.use_hex:
        actual = f"<{to_pdf_hex_utf16be(text)}>"
    else:
        actual = f"({escape_pdf_literal(text)})"

    stream = (
        f"q\nBT\n"
        f"{spec.gs_key} gs\n"
        f"{font_key} {spec.font_size:.4f} Tf\n"
        f"1 0 0 1 {x:.4f} {y:.4f} Tm\n"
        f"/Span << /ActualText {actual} >> BDC\n"
        f"( ) Tj\n"
        f"EMC\nET\nQ\n"
    )
    return stream.encode("utf-8")


def _read_page_content(page: pikepdf.Page) -> bytes:
    """Read all content streams from the page and merge them into one bytes object"""
    contents = page.get("/Contents")
    if contents is None:
        return b""
    streams = [contents] if isinstance(contents, pikepdf.Stream) else contents
    parts = []
    for s in streams:
        if isinstance(s, pikepdf.Stream):
            try:
                parts.append(s.read_bytes())
            except Exception:
                continue
    return b"\n".join(parts)


def _find_safe_insert_positions(content: bytes) -> list[int]:
    """
    Find all safe insertion positions (byte offsets) in the content stream.
    A safe position means after Q\\n (after the graphics state has been restored).
    The injected block itself is wrapped with q...Q, so inserting it there will not affect rendering.
    Also include the beginning (0) and the end of the stream as candidates.
    """
    positions = [0]  # Start of the stream
    # Find the end position of every "Q\n"
    start = 0
    while True:
        idx = content.find(b"Q\n", start)
        if idx == -1:
            break
        pos = idx + 2  # After "Q\n"
        # Make sure the character before Q is a newline or the start of the stream
        # (to avoid false matches such as BQ / EQ)
        if idx == 0 or content[idx - 1:idx] in (b"\n", b"\r", b" "):
            positions.append(pos)
        start = idx + 2
    positions.append(len(content))  # End of the stream
    return positions


def _interleave_injections(original: bytes, injections: list[bytes]) -> bytes:
    """
    Randomly insert injected blocks into safe positions in the original content stream.
    Each injected block is inserted at a randomly selected safe position in the original content,
    so that after parsing, the logical positions of the injected content and the original text are randomly interleaved.
    """
    if not original:
        return b"\n".join(injections)

    safe_positions = _find_safe_insert_positions(original)

    # Randomly choose one safe insertion position for each injected block
    chosen = sorted(
        [(random.choice(safe_positions), inj) for inj in injections],
        key=lambda t: t[0],
        reverse=True,  # Insert from back to front so earlier offsets are not affected
    )

    result = bytearray(original)
    for pos, inj in chosen:
        result[pos:pos] = b"\n" + inj + b"\n"

    return bytes(result)


def inject_invisible_text(
    input_pdf: str,
    output_pdf: str,
    text: str,
    spec: InjectSpec,
):
    with pikepdf.Pdf.open(input_pdf) as pdf:
        if not pdf.pages:
            raise ValueError("PDF has no pages")

        total_pages = len(pdf.pages)
        for page_idx, page in enumerate(pdf.pages):
            # Detect the font independently for each page
            if spec.font_key is None:
                font_key = get_most_frequent_font(page) or "/F1"
            else:
                font_key = spec.font_key

            # Read the original content stream
            original = _read_page_content(page)

            # Generate all injected content
            pw, ph = get_page_dimensions(page)
            margin = spec.font_size
            injections = []
            for _ in range(spec.count):
                x = random.uniform(margin, pw - margin)
                y = random.uniform(margin, ph - margin)
                injections.append(build_invisible_text_stream(text, spec, font_key, x, y))

            # Randomly insert injected blocks into safe positions in the original content stream
            merged = _interleave_injections(original, injections)
            page["/Contents"] = Stream(pdf, merged)

            print(f"  Page {page_idx+1}/{total_pages}: font={font_key}, injected {spec.count} times")

        pdf.save(output_pdf)
        print(f"[Done] {total_pages} pages × {spec.count} times = {total_pages * spec.count} injections, saved to {output_pdf}")


if __name__ == "__main__":
    import os
    import sys
    import json
    import time
    import logging
    from datetime import datetime

    # =========================================================================
    #                           Logging Configuration
    # =========================================================================

    def setup_logger(log_file_path):
        """Configure the logger to output to both console and file"""
        logger = logging.getLogger("MicropixelProcessor")
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()

        formatter = logging.Formatter(
            fmt='%(asctime)s | %(levelname)-8s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        file_handler = logging.FileHandler(log_file_path, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        return logger

    def get_pdf_text_size(pdf_path):
        """Get the total number of bytes in all page content streams of a PDF as a proxy for text size"""
        total_bytes = 0
        try:
            with pikepdf.Pdf.open(pdf_path) as pdf:
                for page in pdf.pages:
                    total_bytes += len(_read_page_content(page))
        except Exception:
            return None
        return total_bytes

    # =========================================================================
    #                           Configuration
    # =========================================================================

    # 12 venues
    venues = [
        "NeurIPS", "ICLR", "ICML", "Nature", "Nature_Biotechnology",
        "NDSS", "USENIX_Security", "CCS", "SP",
        "Advanced_Materials", "Psychological_Review", "ITS"
    ]

    # Defense strategy: explicit / implicit
    defense_strategy = "implicit"

    # Injection spec
    spec = InjectSpec(count=10, font_size=1)

    # Paths
    input_base_dir = "./data/dataset_raw"
    output_base_dir = f"./data/dataset_{defense_strategy}_micropixel"
    prompt_pool_path = f"./configuration/{defense_strategy}_defensive_prompt_pool.json"
    stats_output_path = os.path.join(output_base_dir, f"processing_stats_{defense_strategy}_micropixel.json")

    # Log file (with timestamp)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file_path = os.path.join(output_base_dir, f"processing_log_{defense_strategy}_micropixel_{timestamp}.log")

    os.makedirs(output_base_dir, exist_ok=True)

    logger = setup_logger(log_file_path)

    logger.info("=" * 60)
    logger.info("Micropixel PDF Injection Started")
    logger.info("=" * 60)
    logger.info(f"Defense strategy: {defense_strategy}")
    logger.info(f"Input directory: {input_base_dir}")
    logger.info(f"Output directory: {output_base_dir}")
    logger.info(f"Stats output: {stats_output_path}")
    logger.info(f"Log file: {log_file_path}")
    logger.info(f"InjectSpec: count={spec.count}, font_size={spec.font_size}")
    logger.info(f"Venues to process: {', '.join(venues)}")

    # =========================================================================
    #                   Load the defensive prompt pool
    # =========================================================================

    if not os.path.exists(prompt_pool_path):
        logger.error(f"Defensive prompt pool file not found: {prompt_pool_path}")
        sys.exit(1)
    with open(prompt_pool_path, 'r', encoding='utf-8') as f:
        defensive_prompt_pool = json.load(f)
    logger.info(f"Loaded {len(defensive_prompt_pool)} defensive prompts from {prompt_pool_path}")

    # =========================================================================
    #                           Process all PDFs
    # =========================================================================

    all_stats = []
    total_start_time = time.time()

    for venue in venues:
        logger.info("")
        logger.info("=" * 60)
        logger.info(f"Processing venue: {venue}")
        logger.info("=" * 60)

        # Create output directory
        output_venue_dir = os.path.join(output_base_dir, venue)
        os.makedirs(output_venue_dir, exist_ok=True)
        logger.debug(f"Output directory created/verified: {output_venue_dir}")

        for index in range(10):
            pdf_filename = f"{venue}_{index}.pdf"
            input_pdf = os.path.join(input_base_dir, venue, pdf_filename)
            output_pdf = os.path.join(output_venue_dir, pdf_filename)

            logger.info(f"Processing: {pdf_filename}")

            # Check whether the input file exists
            if not os.path.exists(input_pdf):
                logger.warning(f"  Input file not found: {input_pdf}")
                continue

            # Randomly select one entry from the defensive prompt pool as the injected content
            prompt_entry = random.choice(defensive_prompt_pool)
            text = prompt_entry["content"]
            prompt_id = prompt_entry["defensive_prompt_id"]
            logger.debug(f"  Selected defensive prompt id={prompt_id}, length={len(text)} chars")

            # Before injection: file size & content stream text size
            size_before_bytes = os.path.getsize(input_pdf)
            size_before_mb = size_before_bytes / (1024 * 1024)
            text_size_before = get_pdf_text_size(input_pdf)
            logger.debug(f"  Before: file={size_before_mb:.4f} MB, text_stream={text_size_before} bytes")

            start_time = time.time()

            try:
                inject_invisible_text(
                    input_pdf=input_pdf,
                    output_pdf=output_pdf,
                    text=text,
                    spec=spec,
                )

                end_time = time.time()
                processing_time = end_time - start_time

                # After injection: file size & content stream text size
                size_after_bytes = os.path.getsize(output_pdf)
                size_after_mb = size_after_bytes / (1024 * 1024)
                text_size_after = get_pdf_text_size(output_pdf)

                pdf_stats = {
                    "venue": venue,
                    "index": index,
                    "filename": pdf_filename,
                    "prompt_id": prompt_id,
                    "injected_text_length": len(text),
                    "size_before_mb": round(size_before_mb, 4),
                    "size_after_mb": round(size_after_mb, 4),
                    "size_increase_mb": round(size_after_mb - size_before_mb, 4),
                    "text_size_before_bytes": text_size_before,
                    "text_size_after_bytes": text_size_after,
                    "text_size_increase_bytes": (text_size_after - text_size_before) if (text_size_before is not None and text_size_after is not None) else None,
                    "processing_time_seconds": round(processing_time, 2),
                    "status": "success"
                }
                all_stats.append(pdf_stats)

                size_diff = size_after_mb - size_before_mb
                size_diff_str = f"+{size_diff:.4f}" if size_diff >= 0 else f"{size_diff:.4f}"
                text_diff_str = "N/A"
                if text_size_before is not None and text_size_after is not None:
                    text_diff = text_size_after - text_size_before
                    text_diff_str = f"+{text_diff}" if text_diff >= 0 else f"{text_diff}"

                logger.info(f"  File size: {size_before_mb:.4f} MB -> {size_after_mb:.4f} MB ({size_diff_str} MB)")
                logger.info(f"  Text stream: {text_size_before} -> {text_size_after} bytes ({text_diff_str} bytes)")
                logger.info(f"  Time: {processing_time:.2f}s | Prompt ID: {prompt_id} | Injected text: {len(text)} chars")
                logger.info(f"  [SUCCESS] Saved to: {output_pdf}")

            except Exception as e:
                end_time = time.time()
                processing_time = end_time - start_time

                pdf_stats = {
                    "venue": venue,
                    "index": index,
                    "filename": pdf_filename,
                    "prompt_id": prompt_id,
                    "injected_text_length": len(text),
                    "size_before_mb": round(size_before_mb, 4),
                    "size_after_mb": None,
                    "size_increase_mb": None,
                    "text_size_before_bytes": text_size_before,
                    "text_size_after_bytes": None,
                    "text_size_increase_bytes": None,
                    "processing_time_seconds": round(processing_time, 2),
                    "status": "failed",
                    "error": str(e)
                }
                all_stats.append(pdf_stats)
                logger.error(f"  [FAILED] Error processing {pdf_filename}: {e}")

    # =========================================================================
    #                   Save detailed statistics to JSON
    # =========================================================================

    with open(stats_output_path, 'w', encoding='utf-8') as f:
        json.dump(all_stats, f, ensure_ascii=False, indent=2)

    total_end_time = time.time()
    total_processing_time = total_end_time - total_start_time

    # =========================================================================
    #                   Statistics grouped by venue
    # =========================================================================

    venue_summary = {}
    for venue in venues:
        venue_stats = [s for s in all_stats if s['venue'] == venue and s['status'] == 'success']
        venue_all = [s for s in all_stats if s['venue'] == venue]
        failed_count = len([s for s in venue_all if s['status'] == 'failed'])

        if not venue_stats:
            venue_summary[venue] = {
                "pdf_count": len(venue_all),
                "successful_count": 0,
                "failed_count": failed_count,
                "avg_size_before_mb": None,
                "avg_size_after_mb": None,
                "avg_size_increase_mb": None,
                "avg_text_size_before_bytes": None,
                "avg_text_size_after_bytes": None,
                "avg_text_size_increase_bytes": None,
                "avg_injected_text_length": None,
                "avg_processing_time_seconds": None,
                "total_processing_time_seconds": None,
            }
            continue

        n = len(venue_stats)
        total_size_before = sum(s['size_before_mb'] for s in venue_stats)
        total_size_after = sum(s['size_after_mb'] for s in venue_stats)
        total_size_increase = sum(s['size_increase_mb'] for s in venue_stats)
        total_time = sum(s['processing_time_seconds'] for s in venue_stats)
        total_injected_len = sum(s['injected_text_length'] for s in venue_stats)

        # Text stream size (may contain None values)
        ts_before_vals = [s['text_size_before_bytes'] for s in venue_stats if s['text_size_before_bytes'] is not None]
        ts_after_vals = [s['text_size_after_bytes'] for s in venue_stats if s['text_size_after_bytes'] is not None]
        ts_increase_vals = [s['text_size_increase_bytes'] for s in venue_stats if s['text_size_increase_bytes'] is not None]

        venue_summary[venue] = {
            "pdf_count": len(venue_all),
            "successful_count": n,
            "failed_count": failed_count,
            "avg_size_before_mb": round(total_size_before / n, 4),
            "avg_size_after_mb": round(total_size_after / n, 4),
            "avg_size_increase_mb": round(total_size_increase / n, 4),
            "avg_text_size_before_bytes": round(sum(ts_before_vals) / len(ts_before_vals), 2) if ts_before_vals else None,
            "avg_text_size_after_bytes": round(sum(ts_after_vals) / len(ts_after_vals), 2) if ts_after_vals else None,
            "avg_text_size_increase_bytes": round(sum(ts_increase_vals) / len(ts_increase_vals), 2) if ts_increase_vals else None,
            "avg_injected_text_length": round(total_injected_len / n, 2),
            "avg_processing_time_seconds": round(total_time / n, 2),
            "total_processing_time_seconds": round(total_time, 2),
        }

    # =========================================================================
    #                   Global statistics summary
    # =========================================================================

    successful_stats = [s for s in all_stats if s['status'] == 'success']
    successful_count = len(successful_stats)
    failed_count = len(all_stats) - successful_count

    total_size_before = sum(s['size_before_mb'] for s in all_stats)
    total_size_after = sum(s['size_after_mb'] for s in successful_stats)
    total_injected_len_all = sum(s['injected_text_length'] for s in successful_stats)

    ts_before_all = [s['text_size_before_bytes'] for s in successful_stats if s['text_size_before_bytes'] is not None]
    ts_after_all = [s['text_size_after_bytes'] for s in successful_stats if s['text_size_after_bytes'] is not None]
    ts_increase_all = [s['text_size_increase_bytes'] for s in successful_stats if s['text_size_increase_bytes'] is not None]

    summary_report = {
        "global_summary": {
            "total_pdfs_processed": len(all_stats),
            "successful_count": successful_count,
            "failed_count": failed_count,
            "total_processing_time_seconds": round(total_processing_time, 2),
            "total_size_before_mb": round(total_size_before, 4),
            "total_size_after_mb": round(total_size_after, 4),
            "total_size_change_mb": round(total_size_after - total_size_before, 4),
            "total_text_size_before_bytes": sum(ts_before_all) if ts_before_all else None,
            "total_text_size_after_bytes": sum(ts_after_all) if ts_after_all else None,
            "total_text_size_change_bytes": sum(ts_increase_all) if ts_increase_all else None,
            "avg_size_increase_per_pdf_mb": round((total_size_after - total_size_before) / successful_count, 4) if successful_count > 0 else None,
            "avg_text_size_increase_per_pdf_bytes": round(sum(ts_increase_all) / len(ts_increase_all), 2) if ts_increase_all else None,
            "avg_injected_text_length": round(total_injected_len_all / successful_count, 2) if successful_count > 0 else None,
            "avg_processing_time_per_pdf_seconds": round(total_processing_time / successful_count, 2) if successful_count > 0 else None,
        },
        "venue_summary": venue_summary,
    }

    venue_summary_path = os.path.join(output_base_dir, f"venue_summary_{defense_strategy}_micropixel.json")
    with open(venue_summary_path, 'w', encoding='utf-8') as f:
        json.dump(summary_report, f, ensure_ascii=False, indent=2)

    # =========================================================================
    #                   Output summary to logs
    # =========================================================================

    logger.info("")
    logger.info("=" * 60)
    logger.info("Processing Complete!")
    logger.info("=" * 60)
    logger.info(f"Total PDFs processed: {len(all_stats)}")
    logger.info(f"Successful: {successful_count}")
    logger.info(f"Failed: {failed_count}")
    logger.info(f"Total processing time: {total_processing_time:.2f}s ({total_processing_time/60:.2f} min)")
    logger.info(f"Total file size before: {total_size_before:.4f} MB")
    logger.info(f"Total file size after: {total_size_after:.4f} MB")
    total_size_diff = total_size_after - total_size_before
    total_size_diff_str = f"+{total_size_diff:.4f}" if total_size_diff >= 0 else f"{total_size_diff:.4f}"
    logger.info(f"Total file size change: {total_size_diff_str} MB")
    if ts_before_all and ts_after_all:
        total_text_before = sum(ts_before_all)
        total_text_after = sum(ts_after_all)
        total_text_diff = total_text_after - total_text_before
        total_text_diff_str = f"+{total_text_diff}" if total_text_diff >= 0 else f"{total_text_diff}"
        logger.info(f"Total text stream before: {total_text_before} bytes")
        logger.info(f"Total text stream after: {total_text_after} bytes")
        logger.info(f"Total text stream change: {total_text_diff_str} bytes")

    # Per-venue summary table
    logger.info("")
    logger.info("=" * 90)
    logger.info("Per-Venue Summary")
    logger.info("=" * 90)
    logger.info(f"{'Venue':<25} {'PDFs':>5} {'AvgFileDiff(MB)':>16} {'AvgTextDiff(B)':>16} {'AvgTime(s)':>11}")
    logger.info("-" * 90)
    for venue, stats in venue_summary.items():
        if stats['successful_count'] > 0:
            avg_text_diff_str = f"{stats['avg_text_size_increase_bytes']:>16.2f}" if stats['avg_text_size_increase_bytes'] is not None else f"{'N/A':>16}"
            logger.info(
                f"{venue:<25} {stats['successful_count']:>5} "
                f"{stats['avg_size_increase_mb']:>16.4f} "
                f"{avg_text_diff_str} "
                f"{stats['avg_processing_time_seconds']:>11.2f}"
            )
        else:
            logger.info(f"{venue:<25} {stats['successful_count']:>5} {'N/A':>16} {'N/A':>16} {'N/A':>11}")

    # Global average row
    logger.info("-" * 90)
    g = summary_report['global_summary']
    if g['avg_size_increase_per_pdf_mb'] is not None:
        avg_text_inc = f"{g['avg_text_size_increase_per_pdf_bytes']:>16.2f}" if g['avg_text_size_increase_per_pdf_bytes'] is not None else f"{'N/A':>16}"
        logger.info(
            f"{'ALL (Average)':<25} {successful_count:>5} "
            f"{g['avg_size_increase_per_pdf_mb']:>16.4f} "
            f"{avg_text_inc} "
            f"{g['avg_processing_time_per_pdf_seconds']:>11.2f}"
        )
    else:
        logger.info(f"{'ALL (Average)':<25} {successful_count:>5} {'N/A':>16} {'N/A':>16} {'N/A':>11}")

    logger.info("")
    logger.info(f"Statistics saved to: {stats_output_path}")
    logger.info(f"Venue summary saved to: {venue_summary_path}")
    logger.info(f"Log saved to: {log_file_path}")
