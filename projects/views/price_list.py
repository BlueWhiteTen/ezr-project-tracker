import math
import random
from django.utils import timezone
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.db.models import Q, F as models_F, Count
from django.views.decorators.http import require_POST
from datetime import date, timedelta
import json

from ..models import Project, ProjectLog, Customer, Comment, Message, Notification, TeamMessage, StaffProfile, LeaveRequest, InstallationReport, ReportPhoto, SatisfactionNote, CustomerProfile, ProjectDocument, Product, PickingList, PickingListItem, PickingTemplate, PickingTemplateItem, MaterialPrice, ProjectCost, ProjectCostLine, UprightAccessory, AccessoryOverride, Reminder, FittingCrew, Supplier, PurchaseOrder, PurchaseOrderLine, StockMovement, FittingNote, ProjectQuote, PriceListItem, QuotePhoto, QuoteAttachedPhoto, ProformaInvoice, DeliveryPhase, ProjectPresence
from ..forms import RegisterForm, ProjectForm
from .utils import (_can_edit_prices, stock_lookup_by_code, resolve_stock_item)
from .costing import *  # noqa: F401,F403 — price list seeding needs all costing constants


# ── Auth ──────────────────────────────────────────────────────────────────────

from django.contrib.auth.views import (
    PasswordResetView, PasswordResetDoneView,
    PasswordResetConfirmView, PasswordResetCompleteView,
)

@login_required
def price_list(request):
    # Seed component prices on first load, or migrate from old formats
    old_format = PriceListItem.objects.filter(
        category__in=['ls_frame','ls_shelf','ls_trolley','Posts (TP)','Connectors (TPC)','TFCV Connectors','TFCL Beams','Mesh Panels']
    ).exists() or PriceListItem.objects.filter(code='HRT25').exists() \
      or PriceListItem.objects.filter(category__in=['Hanging Rails','Inboard Hanging Rail Set','25mm Inboard Hanging Rail Tube']).exists()
    has_trimline = PriceListItem.objects.filter(product_line='trimline', category='Shelf Bar').exists()
    has_longspan = PriceListItem.objects.filter(product_line='longspan').exists()
    # Jul 2026 catalog correction: codes matched up against real Stock and
    # several renamed/removed. Force a reseed once if any stale code remains.
    stale_codes = ['TP144','TCTB12','TCTB15','SB27','STM','DTM','SM','SM-SHIM',
                    'STM-SHIM','DTM-SHIM','LSCASTBRKTS','LSCASTBRKT600','LSCASTBRKT900',
                    'LSP3500','LSP4500','LSDB1294','LSCB1000','LSB1150','LSB1500','LSB1800','LSB2700']
    needs_code_fix = PriceListItem.objects.filter(code__in=stale_codes).exists()
    if not PriceListItem.objects.exists() or old_format or not has_trimline or not has_longspan or needs_code_fix:
        from django.db import transaction
        with transaction.atomic():
            PriceListItem.objects.all().delete()
            _seed_price_list()

    can_edit = _can_edit_prices(request.user)
    # Preserve category display order using first-seen order by pk
    from collections import OrderedDict
    items = PriceListItem.objects.all().order_by('pk')

    # Price List now mirrors Stock: buying price and weight are read from the
    # matching Stock item by code (case-insensitive exact match). Any item
    # with no Stock match falls back to its own last-saved value (so nothing
    # goes blank overnight) and is flagged for follow-up.
    stock_by_code = stock_lookup_by_code()
    unmatched = []
    for item in items:
        stock_item = resolve_stock_item(item.code, stock_by_code)
        if stock_item:
            item.stock_price = stock_item.cost_price
            item.stock_weight = stock_item.weight
            item.matched = True
        else:
            item.stock_price = item.price
            item.stock_weight = item.weight
            item.matched = False
            if item.code:
                unmatched.append({'code': item.code, 'label': item.label})

    trimline = OrderedDict()
    longspan = OrderedDict()
    for item in items:
        target = longspan if item.product_line == 'longspan' else trimline
        target.setdefault(item.category, []).append(item)
    # Sort items within each category by sort_order
    for groups in (trimline, longspan):
        for cat in groups:
            groups[cat].sort(key=lambda x: x.sort_order)

    # Board materials (£/sqft) — chipboard (15mm), melamine, 18mm chipboard
    mat_defaults = {'chipboard': 0.50, 'melamine': 0.75, 'chipboard_18mm': 0.75}
    mat_labels = {'chipboard': 'Chipboard (15mm)', 'melamine': 'Melamine', 'chipboard_18mm': 'Chipboard (18mm)'}
    for key, default in mat_defaults.items():
        if not MaterialPrice.objects.filter(name=key).exists():
            MaterialPrice.objects.create(name=key, price_per_sqft=default)
    board_materials = [
        {'name': key, 'label': mat_labels[key],
         'price': float(MaterialPrice.objects.get(name=key).price_per_sqft)}
        for key in ['chipboard', 'chipboard_18mm', 'melamine']
    ]

    return render(request, 'projects/price_list.html', {
        'can_edit': can_edit,
        'trimline_groups': trimline,
        'longspan_groups': longspan,
        'board_materials': board_materials,
        'unmatched_items': unmatched,
    })




@login_required
@require_POST
def price_list_update(request):
    if not _can_edit_prices(request.user):
        return JsonResponse({'error': 'You do not have permission to edit prices.'}, status=403)
    data = json.loads(request.body)
    # Material price update (board materials)
    if data.get('material'):
        mat = get_object_or_404(MaterialPrice, name=data['material'])
        try:
            mat.price_per_sqft = float(data.get('price', 0) or 0)
            mat.save()
        except (ValueError, TypeError):
            return JsonResponse({'error': 'Invalid price'}, status=400)
        return JsonResponse({'ok': True, 'price': float(mat.price_per_sqft)})
    # Component price/weight is now mirrored live from Stock (read-only here) —
    # edit the matching item on the Stock page instead.
    return JsonResponse({'error': 'Component prices and weights are now set on the Stock page and mirrored here automatically.'}, status=400)




def _seed_price_list():
    """Seed component-level prices for Trimline and Longspan from the data tables."""
    from ..longspan_data import (LS_FRAME_PRICES, LS_FRAME_HEIGHTS, LS_FRAME_DEPTHS)

    # Weights (kg each) keyed by code
    W = {
        'TP48':2.0,'TP60':2.3,'TP72':2.7,'TP84':3.2,'TP96':3.6,'TP108':4.1,'TP120':4.5,
        'TPC12':0.5,'TPC15':0.5,'TPC18':0.6,'TPC21':0.8,'TPC24':0.9,'TPC27':1.0,'TPC30':1.2,'TPC36':1.3,
        'TFCV24':1.4,'TFCV30':1.4,'TFCV36':1.5,'TFCV39.5':1.6,'TFCV43.5':1.8,'TFCV48':2.0,
        'TWB36':2.2,'TWB48':3.2,'TWB60':3.8,'TWB72':4.6,
        'TCTB18':0.6,'TCTB24':0.8,'TCTB30':1.0,'TCTB36':1.2,
        'SB18':0.6,'SB24':0.8,'SB30':1.0,'SB36':1.2,
        'TSR':0.0,'TPS':0.1,'TCLC':0.0,'FP':0.1,'TTC':0.0,'TFP':0.01,
        'STMFOOT':0.0,'DTMFOOT':0.0,'SMFOOT':0.12,
    }
    def w(code): return W.get(code)

    # ── TRIMLINE components (in display order) ──
    sort = 0
    for code, price in POSTS.items():
        PriceListItem.objects.create(product_line='trimline', category='Posts',
            code=code, label=code, price=price, weight=w(code), sort_order=sort); sort += 1
    sort = 0
    for code, price in TPCS.items():
        PriceListItem.objects.create(product_line='trimline', category='Post Connectors',
            code=code, label=code, price=price, weight=w(code), sort_order=sort); sort += 1
    sort = 0
    for wd, price in TFCV_CONN.items():
        PriceListItem.objects.create(product_line='trimline', category='TFCV Beams',
            code=f'TFCV{wd}', label=f'TFCV {wd}"', price=price, weight=w(f'TFCV{wd}'), sort_order=sort); sort += 1
    sort = 0
    for wd, price in TWB_PRICES.items():
        PriceListItem.objects.create(product_line='trimline', category='TWB Beams',
            code=f'TWB{wd}', label=f'TWB {wd}"', price=price, weight=w(f'TWB{wd}'), sort_order=sort); sort += 1
    # Inboard Hanging Rail Support (a set = a pair of these + a 25mm tube)
    # HRS21/HRS27 use different Stock codes (HRS21/3N, HRS27/3N) — everything
    # else follows the plain HRS{depth} pattern.
    hrs_code_override = {'21': 'HRS21/3N', '27': 'HRS27/3N'}
    sort = 0
    for d, price in IN_HANG_RAILS.items():
        code = hrs_code_override.get(d, f'HRS{d}')
        PriceListItem.objects.create(product_line='trimline', category='Inboard Hanging Rail Support',
            code=code, label=f'Inboard hanging rail support {d}"', price=price, sort_order=sort); sort += 1
    # 25mm Inboard Hanging Rail Tube
    PriceListItem.objects.create(product_line='trimline', category='Inboard Hanging Rail Support',
        code='25MMFLOCOATTUBE1MM', label='25mm inboard hanging rail tube', price=0, sort_order=sort)
    # Combination Tie Bars — TCTB12/TCTB15 removed, not stocked (Jul 2026)
    tctb = {'TCTB18':2.38,'TCTB24':2.99,'TCTB30':3.56,'TCTB36':4.20}
    sort = 0
    for code, price in tctb.items():
        PriceListItem.objects.create(product_line='trimline', category='Combination Tie Bars',
            code=code, label=code, price=price, weight=w(code), sort_order=sort); sort += 1
    # Shelf Bar — SB27 removed, not stocked (Jul 2026)
    sb = {'SB18':1.62,'SB24':2.16,'SB30':2.70,'SB36':3.18}
    sort = 0
    for code, price in sb.items():
        PriceListItem.objects.create(product_line='trimline', category='Shelf Bar',
            code=code, label=code, price=price, weight=w(code), sort_order=sort); sort += 1
    # Fittings & Misc — DTM-SHIM removed, not stocked (Jul 2026)
    misc = [
        ('TSR','Shelf clips',0.43),('TPS','Post splice',0.49),('TCLC','Connector locking clip',0.25),
        ('FP','Floor plate metal',0.75),('TTC','Top cap',0.27),('TFP','Floor plate plastic',0.27),
        ('STMFOOT','SGL mount foot',1.20),('SHIM2MM','Shim to suit (SGL)',0.56),
        ('DTMFOOT','DBL mount foot',1.31),
        ('SMFOOT','Side mount foot',1.05),('SMSHIM','Shim to suit (side)',0.51),
    ]
    sort = 0
    for code, label, price in misc:
        PriceListItem.objects.create(product_line='trimline', category='Fittings & Misc',
            code=code, label=label, price=price, weight=w(code), sort_order=sort); sort += 1

    # ── LONGSPAN components ──
    # Posts by height
    ls_post_prices = {2000:7.5, 2500:9.0, 3000:10.5, 4000:13.5, 5000:16.5}
    sort = 0
    for h in LS_FRAME_HEIGHTS:
        PriceListItem.objects.create(product_line='longspan', category='Posts',
            code=f'LSP{h}', label=f'Post LSP{h}', price=ls_post_prices.get(h,0), sort_order=sort); sort += 1
    # Horizontal braces by depth
    ls_horiz = {600:'LSHB565', 900:'LSHB865', 1000:'LSHB965', 1200:'LSHB1165'}
    horiz_price = {600:3.0, 900:3.8, 1000:4.0, 1200:4.5}
    sort = 0
    for d, code in ls_horiz.items():
        PriceListItem.objects.create(product_line='longspan', category='Horizontal Braces',
            code=code, label=f'{code} ({d}D)', price=horiz_price.get(d,0), sort_order=sort); sort += 1
    # Diagonal braces by depth (galv). 1000D reuses the 900D part — see note
    # in longspan_data.py's LS_DIAGONAL_BY_DEPTH.
    ls_diag = {600:'LSDB835-G', 900:'LSDB1058-G', 1000:'LSDB1058-G', 1200:'LSDB1312-G'}
    diag_price = {600:3.5, 900:4.2, 1000:4.6, 1200:5.0}
    sort = 0
    for d, code in ls_diag.items():
        PriceListItem.objects.create(product_line='longspan', category='Diagonal Braces',
            code=code, label=f'{code} ({d}D)', price=diag_price.get(d,0), sort_order=sort); sort += 1
    # Beams by width — 1150/1500/1800/2700 use their real Stock codes;
    # 2700 comes in two thicknesses (Z74/Z99), pinned to Z99 (higher stock).
    ls_beams = {950:4.52, 1150:5.31, 1500:6.78, 1800:8.38, 1850:8.96, 2250:10.76, 2400:11.42, 2700:12.78}
    ls_beam_code_override = {1150:'LSB1150-Z61', 1500:'LSB1500-Z61', 1800:'LSB1800-Z64', 2700:'LSB2700-Z99'}
    sort = 0
    for wd, price in ls_beams.items():
        code = ls_beam_code_override.get(wd, f'LSB{wd}')
        PriceListItem.objects.create(product_line='longspan', category='Beams (pair)',
            code=code, label=f'LSB{wd}', price=price, sort_order=sort); sort += 1
    # Chipboard supports by depth. 1000D reuses the 900D part.
    ls_cbs = {600:'LSCB600', 900:'LSCB900', 1000:'LSCB900', 1200:'LSCB1200'}
    cbs_price = {600:0.63, 900:0.913, 1000:1.009, 1200:1.2}
    sort = 0
    for d, code in ls_cbs.items():
        PriceListItem.objects.create(product_line='longspan', category='Chipboard Supports',
            code=code, label=f'{code} ({d}D)', price=cbs_price.get(d,0), sort_order=sort); sort += 1
    # Castor / Trolley
    castor = [('LSCASTOR3BKT/600','Castor bracket 600',3.37),('LSCASTOR3BKT/900','Castor bracket 900',4.46),
              ('LSCASTOR3BRKTS','Castor bracket 1200',6.43),('CASTOR3','Castor wheel',4.76)]
    sort = 0
    for code, label, price in castor:
        PriceListItem.objects.create(product_line='longspan', category='Castor / Trolley',
            code=code, label=label, price=price, sort_order=sort); sort += 1
    # Fixings & Misc
    fixed = [('LSFT','Foot',0.5),('LSP','Spacer',0.3),('LSLP','Locking pin',0.05),
             ('M8X35HEXHD','M8x35 bolt set',0.2),('M8X65HEXHD','M8x65 bolt set',0.25),
             ('LSTC','Top cap',0.4)]
    sort = 0
    for code, label, price in fixed:
        PriceListItem.objects.create(product_line='longspan', category='Fixings & Misc',
            code=code, label=label, price=price, sort_order=sort); sort += 1




def ls_component_prices():
    """Return {code: price} for all longspan components. Stock's buying price
    (matched by code, with colour-variant aliases) takes priority; falls back
    to the Price List's own stored value for any code with no Stock match yet."""
    from .utils import stock_lookup_by_code, resolve_stock_item
    stock_by_code = stock_lookup_by_code()
    result = {}
    for item in PriceListItem.objects.filter(product_line='longspan'):
        stock_item = resolve_stock_item(item.code, stock_by_code)
        result[item.code] = float(stock_item.cost_price) if stock_item else float(item.price)
    return result




def ls_calc_frame_price(height, depth):
    """Frame price = sum of its exploded component prices."""
    from ..longspan_data import ls_explode_frame, LS_FRAME_PRICES
    comp = ls_component_prices()
    if not comp:
        return LS_FRAME_PRICES.get(height, {}).get(depth, 0)
    total = 0
    for code, qty in ls_explode_frame(height, depth, 1).items():
        total += comp.get(code, 0) * qty
    return round(total, 2)




def ls_calc_shelf_price(width, depth):
    """Shelf level price = sum of exploded component prices (beams, pins, board, CBS).
    The chipboard board is priced from the 18mm chipboard £/sqft rate by its area."""
    from ..longspan_data import ls_explode_shelf, LS_SHELF, ls_board_code
    comp = ls_component_prices()
    if not comp:
        info = LS_SHELF.get(f'{width}x{depth}')
        return info['price'] if info else 0
    # 18mm chipboard rate
    mat = {m.name: float(m.price_per_sqft) for m in MaterialPrice.objects.all()}
    chip18 = mat.get('chipboard_18mm', mat.get('melamine', 0.75))
    board_code = ls_board_code(width, depth)
    total = 0
    for code, qty in ls_explode_shelf(width, depth, 1).items():
        if code == board_code or code[0].isdigit():
            # Board — price by area from 18mm chipboard rate.
            # Parse mm dims from the code (e.g. 1495X895)
            import re as _re_b
            m = _re_b.match(r'(\d+)X(\d+)', code)
            if m:
                sqft = (int(m.group(1)) * int(m.group(2))) / 92903.04
                total += sqft * chip18 * qty
            continue
        total += comp.get(code, 0) * qty
    return round(total, 2)




def ls_calc_trolley_price(depth):
    from ..longspan_data import ls_explode_trolley, LS_TROLLEY_COST
    comp = ls_component_prices()
    if not comp:
        return LS_TROLLEY_COST.get(depth, 0)
    total = 0
    for code, qty in ls_explode_trolley(depth, 1).items():
        price = comp.get(code)
        if price is None:
            prod = Product.objects.filter(code__iexact=code).first()
            price = float(prod.cost_price) if prod and prod.cost_price else 0
        total += price * qty
    return round(total, 2)


