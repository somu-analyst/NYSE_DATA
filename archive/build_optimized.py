#!/usr/bin/env python3
"""Build telegram_bot_optimized.py from the duplicate-laden telegram_bot.py.

ARCHIVED — ORIGINAL BUILDER (not in the runtime path). This generated the
production bot from telegram_bot.py. The runtime bot (telegram_bot_optimized.py)
is now edited directly, so this rebuild path is retired and kept for reference.
"""
import re

SRC = '/mnt/c/Users/srini/Options_chain_data/NYSE_DATA/telegram_bot.py'
DST = '/mnt/c/Users/srini/Options_chain_data/NYSE_DATA/telegram_bot_optimized.py'

with open(SRC, 'r') as f:
    lines = f.read().split('\n')

print(f"Total lines: {len(lines)}")

# ── 1. Find the dead copy start ──────────────────────────────────────────────
dead_copy_start = None
name_guard_line = None
for i, line in enumerate(lines):
    stripped = line.strip()
    if name_guard_line is None and stripped.startswith('if __name__'):
        name_guard_line = i
    if stripped.startswith('async def group_stock_detail') and name_guard_line is not None and i > name_guard_line:
        dead_copy_start = i
        break

print(f"First __name__ guard at line {name_guard_line+1}")
print(f"Dead copy starts at line {dead_copy_start+1}")

# ── 2. Extract active section (lines 1 to dead_copy_start-1) ─────────────────
active_section = '\n'.join(lines[:dead_copy_start]) + '\n'
print(f"Active section: {len(active_section):,} chars, {dead_copy_start} lines")

# ── 3. Find helper functions in dead copy by exact name matching ─────────────
def find_func_lines(start_line, end_line, func_name, is_async=False):
    """Find a function's start and end lines within a range."""
    prefix = 'async def' if is_async else 'def'
    start = None
    for i in range(start_line, end_line):
        if lines[i].strip() == f'{prefix} {func_name}(...)' or \
           lines[i].strip().startswith(f'{prefix} {func_name}('):
            # Make sure it's the full signature line
            if '(' in lines[i]:
                start = i
                break
    if start is None:
        return None, None
    # Find end (next def/async def/class at same or lower indent, or blank section)
    end = None
    base_indent = len(lines[start]) - len(lines[start].lstrip())
    for i in range(start + 1, end_line):
        stripped = lines[i].strip()
        if stripped and not stripped.startswith('#'):
            indent = len(lines[i]) - len(lines[i].lstrip())
            if indent <= base_indent and (stripped.startswith('def ') or stripped.startswith('async def ') or stripped.startswith('class ')):
                end = i
                break
    if end is None:
        end = end_line
    return start, end

# Find helpers in dead copy
oi_build_start, oi_build_end = find_func_lines(dead_copy_start, len(lines), '_oi_build_analysis')
oi_key_start, oi_key_end = find_func_lines(dead_copy_start, len(lines), '_oi_key_levels')
classify_start, classify_end = find_func_lines(dead_copy_start, len(lines), '_classify_stock_move')

print(f"_oi_build_analysis: lines {oi_build_start+1}-{oi_build_end+1}" if oi_build_start else "_oi_build_analysis: NOT FOUND")
print(f"_oi_key_levels: lines {oi_key_start+1}-{oi_key_end+1}" if oi_key_start else "_oi_key_levels: NOT FOUND")
print(f"_classify_stock_move: lines {classify_start+1}-{classify_end+1}" if classify_start else "_classify_stock_move: NOT FOUND")

# ── 4. Extract data constants ────────────────────────────────────────────────
# LEGENDS_DATA block
legends_data_start = None
legend_keys_line = None
qoq_action_map_start = None
qoq_action_map_end = None

for i in range(dead_copy_start, len(lines)):
    if lines[i].strip().startswith('LEGENDS_DATA = {'):
        legends_data_start = i
    if lines[i].strip().startswith('LEGEND_KEYS = list(LEGENDS_DATA.keys())'):
        legend_keys_line = i
    if qoq_action_map_start is None and lines[i].strip().startswith('QOQ_ACTION_MAP'):
        qoq_action_map_start = i
    if qoq_action_map_start is not None and qoq_action_map_end is None:
        stripped = lines[i].strip()
        if stripped.startswith('async def ') and i > qoq_action_map_start:
            qoq_action_map_end = i
            break

print(f"LEGENDS_DATA at lines {legends_data_start+1}-{legend_keys_line+1}")
print(f"QOQ_ACTION_MAP at lines {qoq_action_map_start+1}-{qoq_action_map_end+1}")

# ── 5. Extract unique feature functions ─────────────────────────────────────
# Find signal_ticker_detail, oi_build_detail in dead copy
sig_detail_start, _ = find_func_lines(dead_copy_start, len(lines), 'signal_ticker_detail', is_async=True)
oi_detail_start, _ = find_func_lines(dead_copy_start, len(lines), 'oi_build_detail', is_async=True)

# Find legends functions
legends_funcs = {}
for fn in ['legends_menu', 'legends_consensus', 'legends_future', 'legends_qoq',
           'legend_detail', 'gamma_positions_view', 'gamma_log_trade',
           'gamma_advisor_view', 'edge_lab_view', 'recommend_engine',
           'smart_money_hub_report']:
    start, end = find_func_lines(dead_copy_start, len(lines), fn, is_async=True)
    if start:
        legends_funcs[fn] = (start, end)

# Find button_handler and main in dead copy
bh2_start = None
main2_start = None
for i in range(dead_copy_start, len(lines)):
    if lines[i].strip().startswith('async def button_handler') and bh2_start is None:
        bh2_start = i
    if lines[i].strip().startswith('def main()') and main2_start is None:
        main2_start = i

print(f"Second button_handler at line {bh2_start+1}")
print(f"Second main() at line {main2_start+1}")
print(f"Legend functions found: {list(legends_funcs.keys())}")

# ── 6. Build helpers block ───────────────────────────────────────────────────
helpers_lines = []
for start, end in [(oi_build_start, oi_build_end), (oi_key_start, oi_key_end),
                    (classify_start, classify_end)]:
    if start is not None:
        helpers_lines.extend(range(start, end))
helpers_lines.sort()

if helpers_lines:
    helpers_block = '\n'.join([lines[i] for i in helpers_lines]) + '\n'
else:
    helpers_block = ''

print(f"Helpers block: {len(helpers_block):,} chars")

# ── 7. Build data constants block ───────────────────────────────────────────
data_constants_block = '\n'.join(lines[legends_data_start:qoq_action_map_end]) + '\n'
print(f"Data constants block: {len(data_constants_block):,} chars")

# ── 8. Build feature functions block ────────────────────────────────────────
# Get all feature function lines sorted
feature_line_nums = set()
# signal_ticker_detail and oi_build_detail
if sig_detail_start:
    _, end = find_func_lines(dead_copy_start, len(lines), 'signal_ticker_detail', is_async=True)
    for i in range(sig_detail_start, end):
        feature_line_nums.add(i)
if oi_detail_start:
    _, end = find_func_lines(dead_copy_start, len(lines), 'oi_build_detail', is_async=True)
    for i in range(oi_detail_start, end):
        feature_line_nums.add(i)
# legends functions
for fn, (start, end) in legends_funcs.items():
    for i in range(start, end):
        feature_line_nums.add(i)

# Sort and extract
sorted_feature_lines = sorted(feature_line_nums)
feature_funcs_block = '\n'.join([lines[i] for i in sorted_feature_lines]) + '\n'
print(f"Feature functions block: {len(feature_funcs_block):,} chars ({len(sorted_feature_lines)} lines)")

# ── 9. Extract button_handler2 and main2 ────────────────────────────────────
button_handler2_block = '\n'.join(lines[bh2_start:main2_start]) + '\n'
main2_block = '\n'.join(lines[main2_start:]) + '\n'

# ── 10. Find first button_handler location in active section ─────────────────
bh1_active_start = None
for i in range(0, dead_copy_start):
    if lines[i].strip().startswith('async def button_handler'):
        bh1_active_start = i
        break

print(f"First button_handler starts at line {bh1_active_start+1}")

# ── 11. Build the optimized file ─────────────────────────────────────────────
output_parts = []

# Header
output_parts.append('''# telegram_bot_optimized.py
# ─────────────────────────────────────────────────────────────────────────────
# OPTIMIZED VERSION of telegram_bot.py
#
# The original file had 28,863 lines comprising:
#   - Lines 1-12671    : Active code (imports, helpers, handlers, main, guard)
#   - Lines 12672-28863: Dead duplicate copy (never runs under normal polling)
#
# This file contains only the active code, plus unique functions and data
# from the dead copy that were not in the active section.
#
# Removed duplications:
#   - Second set of all functions (lines 12672-24249)
#   - Second button_handler, main(), if __name__ guard
#
# Retained from dead copy:
#   - _oi_key_levels, _classify_stock_move, _oi_build_analysis helpers
#   - LEGENDS_DATA (Q1 2026 legendary investor holdings)
#   - LEGEND_LABELS, LEGENDS_FUTURE_CATCHES, LEGENDS_QOQ, QOQ_ACTION_MAP
#   - legends_menu, legends_consensus, legends_future, legends_qoq
#   - smart_money_hub_report, recommend_engine
#   - gamma_positions_view, gamma_log_trade, gamma_advisor_view
#   - signal_ticker_detail, oi_build_detail
#   - Edge Lab, Smart Money Hub features
# ─────────────────────────────────────────────────────────────────────────────
''')

# Part A: Active section (up to first button_handler)
active_before_bh1 = '\n'.join(lines[:bh1_active_start]) + '\n'
output_parts.append(active_before_bh1)

# Part B: Unique helper functions
output_parts.append('# ═══════════════════════════════════════════════════════════════════════════════\n')
output_parts.append('#  UNIQUE HELPERS (from dead copy)\n')
output_parts.append('# ═══════════════════════════════════════════════════════════════════════════════\n')
output_parts.append(helpers_block)

# Part C: Data constants
output_parts.append('# ═══════════════════════════════════════════════════════════════════════════════\n')
output_parts.append('#  LEGENDARY INVESTORS DATA (Q1 2026 13F Filings)\n')
output_parts.append('# ═══════════════════════════════════════════════════════════════════════════════\n')
output_parts.append(data_constants_block)

# Part D: Feature functions
output_parts.append('# ═══════════════════════════════════════════════════════════════════════════════\n')
output_parts.append('#  UNIQUE FEATURE FUNCTIONS (from dead copy)\n')
output_parts.append('# ═══════════════════════════════════════════════════════════════════════════════\n')
output_parts.append(feature_funcs_block)

# Part E: Merged button_handler (second one has all routes, fix get_db_connection)
bh2_fixed = button_handler2_block.replace('get_db_connection()', 'get_conn()')

output_parts.append('# ═══════════════════════════════════════════════════════════════════════════════\n')
output_parts.append('#  CALLBACK ROUTER (merged - all routes from both copies)\n')
output_parts.append('# ═══════════════════════════════════════════════════════════════════════════════\n')
output_parts.append(bh2_fixed)

# Part F: Single main()
output_parts.append('\n# ═══════════════════════════════════════════════════════════════════════════════\n')
output_parts.append('#  MAIN ENTRY POINT\n')
output_parts.append('# ═══════════════════════════════════════════════════════════════════════════════\n')
output_parts.append(main2_block)

# Write output
output = ''.join(output_parts)
with open(DST, 'w') as f:
    f.write(output)

print(f"\nWrote {len(output):,} chars to {DST}")
print(f"Total lines: {output.count(chr(10))+1:,}")