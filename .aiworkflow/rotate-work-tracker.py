#!/usr/bin/env python3
"""
rotate-work-tracker.py — 3-layer idempotent WORK_TRACKER queue manager.

Layers:
  Active  (hot)  — full working entries with embedded stub blocks
  Log (warm) — stub blocks only, max 30 in WORK_TRACKER
  Archive (cold) — overflow stubs in monthly files

Operations:
  1. COMPRESS: if multiple ## Active entries, keep newest, extract stub
     blocks from older ones into ## Log. Delete old Active entries.
  2. ROTATE: if stubs in ## Log exceed MAX_STUBS, move oldest to
     monthly archive files with **Archived:** timestamp.

Both operations are idempotent. Safe to run any number of times.

Stub block format (Ansible blockinfile-style named markers):
  <!-- BEGIN STUB: YYYY-MM-DD | Title -->
  ### Title | YYYY-MM-DD
  **Meta:** branch | model | `plan-link`
  **Outcome:** status summary
  **Key:** decisions made
  **Pending:** open items or none
  <!-- END STUB: YYYY-MM-DD | Title -->

Usage:
  python rotate-work-tracker.py [--dry-run] [--max N] [--file PATH] [--archive-dir PATH]
"""
import re
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_MAX  = 30
SCRIPT_DIR   = Path(__file__).parent
DEFAULT_FILE = SCRIPT_DIR / "WORK_TRACKER.md"
ARCHIVE_DIR  = SCRIPT_DIR / "archive"

# --- Marker regex ---
# Date group accepts an optional " HH:MM" suffix (CLAUDE.md mandates
# minute-precision markers, e.g. "2026-06-10 03:50"). The \1 backreference
# keeps BEGIN/END markers consistent regardless of whether time is present.
STUB_BLOCK_RE = re.compile(
    r'<!-- BEGIN STUB: (\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2})?) \| (.+?) -->\n'
    r'(.*?)'
    r'<!-- END STUB: \1 \| \2 -->',
    re.DOTALL
)

ACTIVE_ENTRY_RE = re.compile(
    r'^## Active\b[^\n]*\n(.*?)(?=^## |\Z)',
    re.MULTILINE | re.DOTALL
)


def find_stubs_in_text(text: str) -> list[dict]:
    """Find all stub blocks in a text region. Returns list of
    {date, title, full_block, content} sorted by date."""
    stubs = []
    for m in STUB_BLOCK_RE.finditer(text):
        stubs.append({
            'date':       m.group(1),
            'title':      m.group(2),
            'content':    m.group(3).strip(),
            'full_block': m.group(0),
        })
    return sorted(stubs, key=lambda s: s['date'])


def compress(content: str, dry_run: bool) -> str:
    """Phase 1: if multiple Active entries, keep newest, compress rest.
    Returns modified content."""
    # Collect all Active entries with their stub blocks and positions
    entries = []
    for m in ACTIVE_ENTRY_RE.finditer(content):
        stub = STUB_BLOCK_RE.search(m.group(0))
        entries.append({
            'start':    m.start(),
            'end':      m.end(),
            'full':     m.group(0),
            'date':     stub.group(1) if stub else '0000-00-00',
            'stub_blk': stub.group(0) if stub else None,
        })

    if len(entries) <= 1:
        print(f"Compress: {len(entries)} Active entry -- nothing to compress.")
        return content

    # Sort by date, newest last
    entries.sort(key=lambda e: e['date'])
    to_compress = entries[:-1]   # all except newest
    newest      = entries[-1]

    print(f"Compress: {len(entries)} Active entries found, keeping newest ({newest['date']})")

    if dry_run:
        for e in to_compress:
            print(f"  [DRY RUN] would compress: {e['date']}")
        return content

    # Find the ## Log section insertion point
    completed_match = re.search(r'^## Log\s*\n', content, re.MULTILINE)
    if not completed_match:
        print("  ERROR: no '## Log' section found.")
        return content

    insert_pos = completed_match.end()

    # Build stub text to insert (oldest first)
    stubs_to_add = ''
    for e in to_compress:
        if e['stub_blk']:
            stubs_to_add += e['stub_blk'] + '\n\n'
            print(f"  Compressed: {e['date']}")
        else:
            print(f"  WARNING: no stub block in Active entry dated {e['date']}, skipping")

    # Remove old Active entries — ONLY those we successfully extracted a stub for.
    # Never delete an entry whose stub could not be parsed: that would lose history.
    compressed_entries = [e for e in to_compress if e['stub_blk']]
    skipped = [e for e in to_compress if not e['stub_blk']]
    if skipped:
        print(f"  SAFETY: kept {len(skipped)} un-stubbed Active entr(ies) in place to avoid history loss")
    for e in sorted(compressed_entries, key=lambda e: e['start'], reverse=True):
        # Also remove any trailing --- separator
        end = e['end']
        trailing = content[end:end+10].lstrip('\n')
        if trailing.startswith('---'):
            end = end + content[end:].index('---') + 3
            # Also eat the newline after ---
            while end < len(content) and content[end] == '\n':
                end += 1
        content = content[:e['start']] + content[end:]

    # Recalculate insertion point after deletions
    completed_match = re.search(r'^## Log\s*\n', content, re.MULTILINE)
    if completed_match:
        insert_pos = completed_match.end()
        content = content[:insert_pos] + stubs_to_add + content[insert_pos:]

    return content


def rotate(content: str, dry_run: bool, max_stubs: int, archive_dir: Path) -> str:
    """Phase 2: if stubs in Log exceed max, move oldest to archive.
    Returns modified content."""
    # Find Log section
    match = re.search(
        r'^(## Log\s*\n)(.*?)(?=^## |\Z)',
        content, re.MULTILINE | re.DOTALL
    )
    if not match:
        print("Rotate: no '## Log' section found.")
        return content

    section_start = match.start()
    section_header = match.group(1)
    section_body   = match.group(2)

    stubs = find_stubs_in_text(section_body)
    print(f"Rotate: {len(stubs)} stubs in Log (limit: {max_stubs})")

    if len(stubs) <= max_stubs:
        print("  Below limit -- no rotation needed.")
        return content

    n_rotate   = len(stubs) - max_stubs
    to_archive = stubs[:n_rotate]
    to_keep    = stubs[n_rotate:]

    print(f"  Rotating {n_rotate} oldest stubs to archive")

    if dry_run:
        for s in to_archive:
            print(f"    [DRY RUN] would archive: {s['date']} | {s['title']}")
        return content

    # Group by monthly archive file
    archived_ts = datetime.now().astimezone().isoformat()
    by_file: dict[Path, list[dict]] = {}
    for stub in to_archive:
        date = datetime.strptime(stub['date'][:10], '%Y-%m-%d')
        archive_dir.mkdir(exist_ok=True)
        path = archive_dir / f"WORK_TRACKER_{date.strftime('%b%Y').upper()}.md"
        by_file.setdefault(path, []).append(stub)

    # Write to archive files
    for path, batch in by_file.items():
        if not path.exists():
            label = path.stem.replace('WORK_TRACKER_', '')
            path.write_text(f"# WORK_TRACKER -- Archive {label}\n\n", encoding='utf-8')
        with path.open('a', encoding='utf-8') as f:
            for stub in batch:
                f.write(stub['full_block'] + f'\n**Archived:** {archived_ts}\n\n')
        print(f"    -> {path.name} ({len(batch)} stubs)")

    # Rebuild Log section with only kept stubs
    new_body = '\n\n'.join(s['full_block'] for s in to_keep) + '\n' if to_keep else ''
    new_section = section_header + new_body
    content = content[:section_start] + new_section + content[match.end():]

    return content


def main() -> None:
    args        = sys.argv[1:]
    dry_run     = '--dry-run' in args
    max_val     = next((args[i + 1] for i, a in enumerate(args) if a == '--max'), None)
    file_val    = next((args[i + 1] for i, a in enumerate(args) if a == '--file'), None)
    archive_val = next((args[i + 1] for i, a in enumerate(args) if a == '--archive-dir'), None)

    tracker     = Path(file_val) if file_val else DEFAULT_FILE
    max_stubs   = int(max_val) if max_val else DEFAULT_MAX
    archive_dir = Path(archive_val) if archive_val else ARCHIVE_DIR

    if not tracker.exists():
        print(f"File not found: {tracker}")
        sys.exit(1)

    content = tracker.read_text(encoding='utf-8')

    # Phase 1: compress old Active entries
    content = compress(content, dry_run=dry_run)

    # Phase 2: rotate warm queue
    content = rotate(content, dry_run=dry_run, max_stubs=max_stubs, archive_dir=archive_dir)

    if not dry_run:
        tracker.write_text(content, encoding='utf-8')
        print(f"\nWORK_TRACKER updated: {tracker}")


if __name__ == '__main__':
    main()
