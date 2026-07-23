"""
Longspan racking data tables — extracted from the cost calculator and pick list spreadsheets.
Galvanised ("galv") is the default system. Blue uprights differ only in bracing codes and a
couple of fixing counts.
"""

# ── Frame prices: height -> {depth: price} ──
# Cost calculator only lists 600/900/1200. 1000 interpolated (uses 900 price as nearest).
LS_FRAME_PRICES = {
    2000: {600: 18.44, 900: 19.66, 1000: 19.66, 1200: 20.98},
    2500: {600: 22.62, 900: 24.22, 1000: 24.22, 1200: 25.93},
    3000: {600: 27.11, 900: 28.93, 1000: 28.93, 1200: 30.90},
    4000: {600: 34.66, 900: 36.71, 1000: 36.71, 1200: 38.99},
    5000: {600: 42.18, 900: 44.49, 1000: 44.49, 1200: 47.03},
}

LS_FRAME_HEIGHTS = [2000, 2500, 3000, 4000, 5000]
LS_FRAME_DEPTHS = [600, 900, 1000, 1200]

# ── Complete shelf level prices: "WIDTHxDEPTH" -> {price, cbs} ──
# cbs = number of chipboard supports included for that level size
LS_SHELF = {
    '950x600':   {'price': 12.65, 'cbs': 0},
    '950x900':   {'price': 19.94, 'cbs': 2},
    '950x1000':  {'price': 20.50, 'cbs': 2},
    '950x1200':  {'price': 23.16, 'cbs': 2},
    '1150x600':  {'price': 14.91, 'cbs': 0},
    '1150x900':  {'price': 22.62, 'cbs': 2},
    '1150x1000': {'price': 23.50, 'cbs': 2},
    '1150x1200': {'price': 26.26, 'cbs': 2},
    '1500x600':  {'price': 20.93, 'cbs': 1},
    '1500x900':  {'price': 27.35, 'cbs': 2},
    '1500x1000': {'price': 28.50, 'cbs': 2},
    '1500x1200': {'price': 34.97, 'cbs': 3},
    '1800x600':  {'price': 22.56, 'cbs': 0},
    '1800x900':  {'price': 31.63, 'cbs': 2},
    '1800x1000': {'price': 32.80, 'cbs': 2},
    '1800x1200': {'price': 43.15, 'cbs': 4},
    '1850x600':  {'price': 23.00, 'cbs': 0},
    '1850x900':  {'price': 32.00, 'cbs': 2},
    '1850x1000': {'price': 33.00, 'cbs': 2},
    '1850x1200': {'price': 43.50, 'cbs': 4},
    '2250x600':  {'price': 31.16, 'cbs': 1},
    '2250x900':  {'price': 41.80, 'cbs': 3},
    '2250x1000': {'price': 43.00, 'cbs': 3},
    '2250x1200': {'price': 51.61, 'cbs': 4},
    '2400x600':  {'price': 33.01, 'cbs': 1},
    '2400x900':  {'price': 43.96, 'cbs': 3},
    '2400x1000': {'price': 45.00, 'cbs': 3},
    '2400x1200': {'price': 54.09, 'cbs': 4},
    '2700x600':  {'price': 43.12, 'cbs': 2},
    '2700x900':  {'price': 52.86, 'cbs': 3},
    '2700x1000': {'price': 54.00, 'cbs': 3},
    '2700x1200': {'price': 63.80, 'cbs': 4},
}

LS_SHELF_WIDTHS = [950, 1150, 1500, 1800, 1850, 2250, 2400, 2700]
LS_SHELF_DEPTHS = [600, 900, 1000, 1200]

# ── Trolley (castor bracket assembly): depth -> cost per trolley (based on 2 frames) ──
LS_TROLLEY_COST = {600: 27.78, 900: 29.96, 1000: 31.93, 1200: 33.90}

# ── Frame breakdown: per-frame component counts by height (GALV system) ──
# columns from the galv sheet: 2000,2500,3000,3500,4000,4500 (5000 extrapolated from 4500)
LS_FRAME_BREAKDOWN_GALV = {
    2000: {'post': 2, 'horizontal': 2, 'diagonal': 3, 'foot': 2, 'spacer': 2, 'm8x30': 6, 'm8x65': 2},
    2500: {'post': 2, 'horizontal': 3, 'diagonal': 3, 'foot': 2, 'spacer': 4, 'm8x30': 8, 'm8x65': 2},
    3000: {'post': 2, 'horizontal': 3, 'diagonal': 4, 'foot': 2, 'spacer': 4, 'm8x30': 9, 'm8x65': 2},
    3500: {'post': 2, 'horizontal': 2, 'diagonal': 5, 'foot': 2, 'spacer': 2, 'm8x30': 8, 'm8x65': 2},
    4000: {'post': 2, 'horizontal': 2, 'diagonal': 6, 'foot': 2, 'spacer': 2, 'm8x30': 9, 'm8x65': 2},
    4500: {'post': 2, 'horizontal': 2, 'diagonal': 7, 'foot': 2, 'spacer': 2, 'm8x30': 10, 'm8x65': 2},
    5000: {'post': 2, 'horizontal': 2, 'diagonal': 8, 'foot': 2, 'spacer': 2, 'm8x30': 11, 'm8x65': 2},
}

# Blue system phased out — galvanised only.


# ── Component codes ──
def ls_post_code(height):
    # Galvanised posts — real Stock code has a -G suffix (Jul 2026)
    return f'LSP{height}-G'

# Horizontal brace code by depth (exact codes from stock)
LS_HORIZONTAL_BY_DEPTH = {600: 'LSHB565', 900: 'LSHB865', 1000: 'LSHB965', 1200: 'LSHB1165'}

# Galvanised diagonal brace code by DEPTH (galv is the only system now)
# 600/900/1200 confirmed real Stock codes. 1000D has no distinct Stock code of
# its own (per Tasos, Jul 2026) — reuses the 900D part, same as frame/shelf
# pricing already treats 1000D as effectively equal to 900D elsewhere in this
# file. Revisit if a genuine 1000D-specific part gets stocked separately.
LS_DIAGONAL_BY_DEPTH = {600: 'LSDB835-G', 900: 'LSDB1058-G', 1000: 'LSDB1058-G', 1200: 'LSDB1312-G'}

# Beam code by width. 2700 comes in two thicknesses (Z74/Z99) at the same
# price — pinned to Z99 since that's the one actually held in stock (Jul 2026).
LS_BEAM_BY_WIDTH = {
    950: 'LSB950', 1150: 'LSB1150-Z61', 1500: 'LSB1500-Z61', 1800: 'LSB1800-Z64',
    1850: 'LSB1850', 2250: 'LSB2250', 2400: 'LSB2400', 2700: 'LSB2700-Z99',
}

# Chipboard support code by depth. 1000D has no distinct Stock code of its
# own — reuses the 900D part (same reasoning as the diagonal brace above).
LS_CBS_BY_DEPTH = {600: 'LSCB600', 900: 'LSCB900', 1000: 'LSCB900', 1200: 'LSCB1200'}

# Board code by width x depth (the chipboard that sits on the level)
# Boards are sized slightly under nominal: 945/1145/1495/1795/1845/2245/2395 x 595/895/995
def ls_board_code(width, depth):
    w_map = {950: 945, 1150: 1145, 1500: 1495, 1800: 1795, 1850: 1845,
             2250: 2245, 2400: 2395, 2700: 1350}  # 2700 split into 2x 1350 pieces
    d_map = {600: 595, 900: 895, 1000: 995, 1200: 1195}
    bw = w_map.get(width, width)
    bd = d_map.get(depth, depth)
    return f'{bw}X{bd}'

# Fixed component codes
LS_FOOT = 'LSFT'
LS_SPACER = 'LSP'
LS_BOLT_M8X30 = 'M8X35HEXHD'  # bolt/nut/washer set (frame uses M8x35)
LS_BOLT_M8X65 = 'M8X65HEXHD'
LS_TOP_CAP = 'LSTC'
LS_PIN = 'LSLP'             # beam locking pins

# Misc fixings always per frame
LS_ANCHOR = 'SA10X75'        # SA10x75/80 or DP10050

# Castor assembly per trolley (from cost calculator + pick list)
LS_CASTOR_WHEEL = 'CASTOR3'
LS_CASTOR_BRACKET_BY_DEPTH = {600: 'LSCASTOR3BKT/600', 900: 'LSCASTOR3BKT/900', 1000: 'LSCASTOR3BKT/900', 1200: 'LSCASTOR3BRKTS'}


def ls_explode_trolley(depth, qty):
    """Castor bracket assembly per trolley (based on 2 frames).
    Per trolley: 2 castor brackets (by depth), 4 castor wheels, plus fixings.
    NB: the frame(s) themselves are added as separate frame lines."""
    items = {}
    bracket = LS_CASTOR_BRACKET_BY_DEPTH.get(depth, f'LSCASTBRKT{depth}')
    items[bracket] = items.get(bracket, 0) + 4 * qty          # 4 brackets per trolley (2 per frame x 2 frames)
    items[LS_CASTOR_WHEEL] = items.get(LS_CASTOR_WHEEL, 0) + 4 * qty
    items['M8X70'] = items.get('M8X70', 0) + 8 * qty          # castor bolts
    items['M8NYLOCK'] = items.get('M8NYLOCK', 0) + 8 * qty
    items['M12X35'] = items.get('M12X35', 0) + 4 * qty
    items['M12WASHER'] = items.get('M12WASHER', 0) + 4 * qty
    items['M12NYLOCK'] = items.get('M12NYLOCK', 0) + 4 * qty
    return items


def ls_frame_price(height, depth):
    hp = LS_FRAME_PRICES.get(height, {})
    return hp.get(depth, 0)


def ls_explode_frame(height, depth, qty, system='galv'):
    """Return dict of {code: qty} for a longspan (galvanised) frame."""
    items = {}
    breakdown = LS_FRAME_BREAKDOWN_GALV.get(height)
    if not breakdown:
        return items
    horiz_code = LS_HORIZONTAL_BY_DEPTH.get(depth, f'LSHB{depth}')
    diag_code = LS_DIAGONAL_BY_DEPTH.get(depth, 'LSDB1312-G')

    def add(code, n):
        if n:
            items[code] = items.get(code, 0) + n * qty

    add(ls_post_code(height), breakdown['post'])
    add(horiz_code, breakdown['horizontal'])
    add(diag_code, breakdown['diagonal'])
    add(LS_FOOT, breakdown['foot'])
    add(LS_SPACER, breakdown['spacer'])
    add(LS_BOLT_M8X30, breakdown['m8x30'])
    add(LS_BOLT_M8X65, breakdown['m8x65'])
    return items


def ls_explode_shelf(width, depth, qty):
    """Return dict of {code: qty} for a complete longspan shelf level."""
    items = {}
    key = f'{width}x{depth}'
    info = LS_SHELF.get(key, LS_SHELF.get(f'{width}x{depth}'))
    beam = LS_BEAM_BY_WIDTH.get(width, f'LSB{width}')
    cbs_code = LS_CBS_BY_DEPTH.get(depth, f'LSCBS{depth}')
    board = ls_board_code(width, depth)

    items[beam] = items.get(beam, 0) + 1 * qty          # 1 pair of beams per level
    items['LSLP'] = items.get('LSLP', 0) + 4 * qty      # 4 locking pins per level
    # 2700mm levels = board in 2 pieces
    board_qty = 2 if width == 2700 else 1
    items[board] = items.get(board, 0) + board_qty * qty
    # Chipboard supports
    cbs_qty = info['cbs'] if info else 2
    if cbs_qty:
        items[cbs_code] = items.get(cbs_code, 0) + cbs_qty * qty
    return items
