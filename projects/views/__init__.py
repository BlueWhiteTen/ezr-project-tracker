"""
Projects views package.
All views are imported here so urls.py can continue to use `views.function_name`
without any changes.
"""
from .utils import (
    _snap, _log_changes, _add_working_days, _handle_quoted_status_reminders,
    _log_po_event, _calc_sell_price, _initials, _next_project_number,
    _po_locked_response, _picking_locked_response, _can_edit_prices, _can_view_reports,
)
from .auth import (
    login_view, register_view, logout_view, create_superuser_once,
    password_reset_request, password_reset_done,
    password_reset_confirm, password_reset_complete,
)
from .reporting import (
    home, daily_accounts_report, activity_log, week_view,
    monthly_summary, calendar_view, board_view, so_search,
    sales_summary, sales_summary_export,
)
from .projects import (
    project_create, project_quick_create, project_edit, project_delete,
    project_data, project_quick_status, project_payment_method, project_add_comment,
    project_presence, project_duplicate, bulk_status_update,
    save_dashboard_filters, dashboard, add_comment_with_tags,
    projects_search, project_address_save,
)
from .reminders import (
    reminder_add, reminder_delete, reminder_dismiss, reminders_list, check_reminders,
)
from .customers import (
    customer_autocomplete, customer_history, customer_list, customer_import,
    customer_detail, customer_create, customer_delete,
)
from .suppliers import (
    supplier_list, supplier_import, supplier_detail,
    supplier_create, supplier_delete, supplier_api_list,
    supplier_documents, supplier_document_download, supplier_document_delete,
)
from .purchasing import (
    po_list, po_detail, po_print, po_create, po_update,
    po_cancel, po_lock, po_unlock, po_line_add, po_line_update,
    po_line_delete, po_receive, po_receive_partial, po_unreceive,
    delivery_phase_add, delivery_phase_update, delivery_phase_delete,
    project_search,
)
from .messaging import (
    notification_count, notifications_view, notification_dismiss, inbox, conversation_poll,
    conversation, team_chat, team_chat_poll,
)
from .staff import (
    staff_directory, staff_profile, get_bank_holidays,
    leave_overview, leave_add, leave_delete,
)
from .costing import (
    project_cost, project_cost_save, cost_line_add, cost_line_delete,
    cost_line_update, material_price_update, cost_option_add, project_cost_stock_reference,
    cost_option_delete, cost_option_accept, cost_option_rename, project_cost_refresh_prices,
    project_cost_print, product_toggle_active, cost_accessory_toggle,
    cost_accessory_override, cost_extras_save, cost_wall_fixings_save,
    generate_picking_reference, calc_in_hang_price, trimline_component_prices,
    calc_frame_price, calc_shelf_price, fitting_crew_api, satisfaction_note,
)
from .picking import (
    picking_list_view, picking_list_create, picking_list_save,
    picking_item_add, picking_item_delete, picking_item_toggle,
    picking_status, picking_item_add_ns, picking_allocate,
    picking_deallocate, picking_item_add_msg, template_list,
    template_detail, template_create, template_duplicate,
    template_import_excel, picking_apply_template, picking_list_print,
    picking_item_qty, picking_from_costing, picking_from_costing_save,
    picking_fitting_notes, picking_fitting_notes_pdf, picking_list_delete,
)
from .quotes import (
    customer_quote, quote_refresh_price, proforma_invoice,
    quote_photo_attach, quote_attached_photo_update,
    quote_attached_photo_delete, quote_attached_photo_file,
    quote_photos_list, quote_photo_upload, quote_photo_update,
    quote_photo_delete, quote_photo_file,
)
from .photos import (
    install_report, delete_report_photo, delete_satisfaction_note,
    report_photo_upload, report_sat_upload, report_photo_file,
    photo_library, photo_library_project, satisfaction_note_file,
    fitting_notes_list, fitting_note_upload, fitting_note_delete,
    fitting_note_update, fitting_note_download,
)
from .stock import (
    project_documents, document_download, document_delete,
    stock_list, product_search_api, stock_activity, stock_price_history, stock_adjust, stock_import, stock_create, stock_print, stock_bulk_category,
    stock_draft_pos_preview, stock_draft_pos_generate,
)
from .pdf import (
    po_pdf, picking_list_pdf, proforma_pdf,
)
from .price_list import (
    price_list, price_list_update, _seed_price_list,
    ls_component_prices, ls_calc_frame_price, ls_calc_shelf_price,
    ls_calc_trolley_price,
)
