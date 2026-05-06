import re
import pikepdf
from pikepdf import Name, Dictionary, Stream, Array

def utf16be_hex_with_bom(s: str) -> str:
    b = b"\xFE\xFF" + s.encode("utf-16-be")
    return b.hex().upper()

def pdf_escape_literal(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

def normalize_text_for_pdf(text: str) -> str:
    """Normalize text for PDF insertion.
    1. Replace newlines, tabs, and non-breaking spaces with regular spaces.
    2. Collapse consecutive whitespace into a single space.
    """
    text = text.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ').replace('\xa0', ' ')
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def _pick_unique_font_key(pdf: pikepdf.Pdf, prefix: str = "/_Hv") -> str:
    """Generate a unique font key that does not conflict with existing page font keys in the PDF."""
    existing = set()
    for page in pdf.pages:
        res = page.get("/Resources", None)
        if res is None:
            continue
        fd = res.get("/Font", None)
        if fd is None:
            continue
        for k in fd.keys():
            existing.add(str(k))
    if prefix not in existing:
        return prefix
    for i in range(100):
        candidate = f"{prefix}{i}"
        if candidate not in existing:
            return candidate
    return "/_HvFb"


def ensure_helvetica_font(pdf: pikepdf.Pdf, page: pikepdf.Page, font_key="/F1"):
    # 1) Resources dictionary
    res = page.get("/Resources", None)
    if res is None:
        res = Dictionary()
        page["/Resources"] = res
    else:
        if not isinstance(res, Dictionary):
            res = Dictionary(res)
            page["/Resources"] = res

    # 2) Font dictionary
    font_dict = res.get("/Font", None)
    if font_dict is None:
        font_dict = Dictionary()
        res["/Font"] = font_dict
    else:
        if not isinstance(font_dict, Dictionary):
            font_dict = Dictionary(font_dict)
            res["/Font"] = font_dict

    # 3) Insert Helvetica
    if font_key not in font_dict:
        font_dict[font_key] = Dictionary({
            "/Type": Name("/Font"),
            "/Subtype": Name("/Type1"),
            "/BaseFont": Name("/Helvetica"),
            "/Encoding": Name("/WinAnsiEncoding"),
        })

def _read_page_content(page: pikepdf.Page) -> bytes:
    """Read all page content streams and merge them into a single bytes object."""
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
    Safe positions are immediately after Q\n, plus the beginning and end of the stream.
    Since each injected block is wrapped with q...Q, inserting at these positions helps avoid affecting the original rendering.
    """
    positions = [0]
    start = 0
    while True:
        idx = content.find(b"Q\n", start)
        if idx == -1:
            break
        pos = idx + 2
        if idx == 0 or content[idx - 1:idx] in (b"\n", b"\r", b" "):
            positions.append(pos)
        start = idx + 2
    positions.append(len(content))
    return positions


def _interleave_injections(original: bytes, injections: list[bytes]) -> bytes:
    """
    Insert multiple injection blocks into the original content stream instead of creating a new /Contents stream.
    To stay aligned with micropixel, injections are randomly placed at safe positions; if the original stream is empty, the blocks are concatenated directly.
    """
    if not original:
        return b"\n".join(injections)

    import random

    safe_positions = _find_safe_insert_positions(original)
    chosen = sorted(
        [(random.choice(safe_positions), inj) for inj in injections],
        key=lambda t: t[0],
        reverse=True,
    )

    result = bytearray(original)
    for pos, inj in chosen:
        result[pos:pos] = b"\n" + inj + b"\n"

    return bytes(result)


def inject_actualtext(
    input_pdf,
    output_pdf,
    visible_text_A,
    actual_text_B,
    x,
    y,
    font_size=12,
    per_page_count=1,
    y_step=14,
):
    with pikepdf.Pdf.open(input_pdf) as pdf:
        # Normalize hidden text: convert newlines, tabs, and non-breaking spaces to regular spaces and collapse repeated whitespace
        clean_text_B = normalize_text_for_pdf(actual_text_B)
        b_hex = utf16be_hex_with_bom(clean_text_B)
        A_escaped = pdf_escape_literal(visible_text_A)

        # Always inject Helvetica using a unique key that does not conflict with existing fonts
        font_key = _pick_unique_font_key(pdf)

        for page in pdf.pages:
            ensure_helvetica_font(pdf, page, font_key=font_key)

            original = _read_page_content(page)
            injections = []
            for i in range(max(1, int(per_page_count))):
                y_pos = y - (i * y_step)
                content = (
                    f"q\n"
                    f"/Span << /ActualText <{b_hex}> >> BDC\n"
                    f"BT\n"
                    f"{font_key} {font_size} Tf\n"
                    f"1 0 0 1 {x} {y_pos} Tm\n"
                    f"({A_escaped}) Tj\n"
                    f"ET\n"
                    f"EMC\n"
                    f"Q"
                ).encode("utf-8")
                injections.append(content)

            merged = _interleave_injections(original, injections)
            page["/Contents"] = Stream(pdf, merged)
        pdf.save(output_pdf)

if __name__ == "__main__":
    import os
    import sys
    import json
    import time
    import random
    import logging
    from datetime import datetime

    # =========================================================================
    #                           Logging configuration
    # =========================================================================

    def setup_logger(log_file_path):
        """Configure a logger that writes to both console and file."""
        logger = logging.getLogger("VisualDeceptionProcessor")
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
        """Get the total byte size of all page content streams in a PDF as a text-size indicator."""
        total_bytes = 0
        try:
            with pikepdf.Pdf.open(pdf_path) as pdf:
                for page in pdf.pages:
                    contents = page.get("/Contents")
                    if contents is None:
                        continue
                    streams = [contents] if isinstance(contents, pikepdf.Stream) else contents
                    for s in streams:
                        if isinstance(s, pikepdf.Stream):
                            try:
                                total_bytes += len(s.read_bytes())
                            except Exception:
                                continue
        except Exception:
            return None
        return total_bytes

    def sample_and_concat_prompts(prompt_pool, sample_count=10):
        """Randomly sample sample_count prompts from the pool and concatenate them into one string.
        Return (concatenated_text, selected_prompt_id_list)."""
        selected = random.sample(prompt_pool, min(sample_count, len(prompt_pool)))
        ids = [p["defensive_prompt_id"] for p in selected]
        text = " ".join(p["content"] for p in selected)
        return text, ids

    # =========================================================================
    #                           Configuration
    # =========================================================================

    # 12 venues
    venues = [
        "NeurIPS", "ICLR", "ICML", "Nature", "Nature_Biotechnology",
        "NDSS", "USENIX_Security", "CCS", "SP",
        "Advanced_Materials", "Psychological_Review", "ITS"
    ]

    # Mapping from venue name to visible text (used for visible_text_A)
    venue_visible_text = {
        "NeurIPS": "Neural Information Processing Systems",
        "ICLR": "International Conference on Learning Representations",
        "ICML": "International Conference on Machine Learning",
        "Nature": "Nature",
        "Nature_Biotechnology": "Nature Biotechnology",
        "NDSS": "NDSS",
        "USENIX_Security": "USENIX Security",
        "CCS": "ACM CCS",
        "SP": "IEEE S&P",
        "Advanced_Materials": "Advanced Materials",
        "Psychological_Review": "Psychological Review",
        "ITS": "IEEE Transactions on ITS",
    }

    # Mapping from venue name to font size
    venue_font_size = {
        "NeurIPS": 8,
        "ICLR": 10,
        "ICML": 10,
        "Nature": 12,
        "Nature_Biotechnology": 10,
        "NDSS": 10,
        "USENIX_Security": 10,
        "CCS": 10,
        "SP": 10,
        "Advanced_Materials": 10,
        "Psychological_Review": 14,
        "ITS": 12,
    }

    # Mapping from venue name to injection X coordinate
    venue_position_x = {
        "NeurIPS": 108,
        "ICLR": 108,
        "ICML": 55,
        "Nature": 285,
        "Nature_Biotechnology": 260,
        "NDSS": 48,
        "USENIX_Security": 55,
        "CCS": 55,
        "SP": 55,
        "Advanced_Materials": 250,
        "Psychological_Review": 235,
        "ITS": 240,
    }

    # Mapping from venue name to injection Y coordinate
    venue_position_y = {
        "NeurIPS": 730,
        "ICLR": 730,
        "ICML": 750,
        "Nature": 755,
        "Nature_Biotechnology": 20,
        "NDSS": 750,
        "USENIX_Security": 735,
        "CCS": 740,
        "SP": 730,
        "Advanced_Materials": 25,
        "Psychological_Review": 750,
        "ITS": 770,
    }

    # Defense strategy: explicit / implicit (set manually)
    defense_strategy = "implicit"

    # Number of prompts randomly sampled each time
    prompt_sample_count = 10

    # Number of repeated insertions per page
    injection_repeat_count = 5

    # Step size in the y direction
    y_step_ = 0

    # Paths
    input_base_dir = "./data/dataset_raw"
    output_base_dir = f"./data/dataset_{defense_strategy}_visual_deception"
    prompt_pool_path = f"./configuration/{defense_strategy}_defensive_prompt_pool.json"
    stats_output_path = os.path.join(output_base_dir, f"processing_stats_{defense_strategy}_visual_deception.json")

    # Log file (with timestamp)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file_path = os.path.join(output_base_dir, f"processing_log_{defense_strategy}_visual_deception_{timestamp}.log")

    os.makedirs(output_base_dir, exist_ok=True)

    logger = setup_logger(log_file_path)

    logger.info("=" * 60)
    logger.info("Visual Deception PDF Injection Started")
    logger.info("=" * 60)
    logger.info(f"Defense strategy: {defense_strategy}")
    logger.info(f"Prompt sample count: {prompt_sample_count}")
    logger.info(f"Injection repeat count (per page): {injection_repeat_count}")
    logger.info(f"Font size / Position X / Position Y: per-venue mapping, y_step: {y_step_}")
    logger.info(f"Input directory: {input_base_dir}")
    logger.info(f"Output directory: {output_base_dir}")
    logger.info(f"Stats output: {stats_output_path}")
    logger.info(f"Log file: {log_file_path}")
    logger.info(f"Venues to process: {', '.join(venues)}")

    # =========================================================================
    #                   Load defensive prompt pool
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

        visible_text = venue_visible_text.get(venue, venue)
        font_size_ = venue_font_size.get(venue, 8)
        position_x = venue_position_x.get(venue, 55)
        position_y = venue_position_y.get(venue, 750)
        logger.info(f"  Venue params: font_size={font_size_}, position=({position_x}, {position_y}), y_step={y_step_}")

        for index in range(10):
            pdf_filename = f"{venue}_{index}.pdf"
            input_pdf = os.path.join(input_base_dir, venue, pdf_filename)
            output_pdf = os.path.join(output_venue_dir, pdf_filename)

            logger.info(f"Processing: {pdf_filename}")

            # Check whether the input file exists
            if not os.path.exists(input_pdf):
                logger.warning(f"  Input file not found: {input_pdf}")
                continue

            # Randomly sample 10 prompts from the defensive prompt pool and concatenate them
            text, selected_ids = sample_and_concat_prompts(
                defensive_prompt_pool, sample_count=prompt_sample_count
            )
            logger.debug(f"  Selected {len(selected_ids)} prompts, ids={selected_ids}, total length={len(text)} chars")

            # Before injection: file size and content stream size
            size_before_bytes = os.path.getsize(input_pdf)
            size_before_mb = size_before_bytes / (1024 * 1024)
            text_size_before = get_pdf_text_size(input_pdf)
            logger.debug(f"  Before: file={size_before_mb:.4f} MB, text_stream={text_size_before} bytes")

            start_time = time.time()

            try:
                inject_actualtext(
                    input_pdf=input_pdf,
                    output_pdf=output_pdf,
                    visible_text_A=visible_text,
                    actual_text_B=text,
                    x=position_x,
                    y=position_y,
                    font_size=font_size_,
                    per_page_count=injection_repeat_count,
                    y_step=y_step_,
                )

                end_time = time.time()
                processing_time = end_time - start_time

                # After injection: file size and content stream size
                size_after_bytes = os.path.getsize(output_pdf)
                size_after_mb = size_after_bytes / (1024 * 1024)
                text_size_after = get_pdf_text_size(output_pdf)

                pdf_stats = {
                    "venue": venue,
                    "index": index,
                    "filename": pdf_filename,
                    "visible_text": visible_text,
                    "selected_prompt_ids": selected_ids,
                    "prompt_sample_count": len(selected_ids),
                    "injected_text_length": len(text),
                    "injection_repeat_count": injection_repeat_count,
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
                logger.info(f"  Time: {processing_time:.2f}s | Prompts: {len(selected_ids)} sampled, {injection_repeat_count}x repeat | Text: {len(text)} chars")
                logger.info(f"  [SUCCESS] Saved to: {output_pdf}")

            except Exception as e:
                end_time = time.time()
                processing_time = end_time - start_time

                pdf_stats = {
                    "venue": venue,
                    "index": index,
                    "filename": pdf_filename,
                    "visible_text": visible_text,
                    "selected_prompt_ids": selected_ids,
                    "prompt_sample_count": len(selected_ids),
                    "injected_text_length": len(text),
                    "injection_repeat_count": injection_repeat_count,
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
    #                   Venue-based statistical analysis
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

        # Content stream sizes (may contain None values)
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
    #                   Global summary statistics
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

    venue_summary_path = os.path.join(output_base_dir, f"venue_summary_{defense_strategy}_visual_deception.json")
    with open(venue_summary_path, 'w', encoding='utf-8') as f:
        json.dump(summary_report, f, ensure_ascii=False, indent=2)

    # =========================================================================
    #                   Log output summary
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
