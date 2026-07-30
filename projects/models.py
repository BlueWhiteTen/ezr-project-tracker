from django.db import models
from django.contrib.auth.models import User


class Customer(models.Model):
    name       = models.CharField(max_length=200, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Project(models.Model):
    STATUS_CHOICES = [
        ('enquiry',      'Enquiry'),
        ('quoted',       'Quoted'),
        ('order_received','Order Received'),
        ('processed',    'Processed'),
        ('part_delivered','Part Delivered'),
        ('completed',    'Completed'),
        ('on_hold',      'On Hold'),
        ('cancelled',    'Cancelled'),
    ]

    PAYMENT_CHOICES = [
        ('account',  'On Account'),
        ('proforma', 'Proforma'),
        ('bacs',     'BACs'),
        ('chaps',    'CHAPs'),
        ('sagepay',  'Sagepay'),
        ('paypal',   'PayPal'),
        ('cc',       'CC'),
        ('dc',       'DC'),
        ('chq',      'CHQ'),
        ('cash',     'Cash'),
        ('cod',      'COD'),
    ]

    INSTALL_DATE_TYPE = [('exact','Exact Date'),('month','Month')]
    MONTH_PART        = [('','Any'),('early','Early'),('mid','Mid'),('end','End')]

    project_name   = models.CharField(max_length=200)
    customer       = models.CharField(max_length=200)
    customer_profile = models.ForeignKey('CustomerProfile', null=True, blank=True, on_delete=models.SET_NULL, related_name='projects',
        help_text='Real link to the Customer record. The customer text field is kept in sync automatically for backward compatibility.')
    location       = models.CharField(max_length=300, blank=True)
    description    = models.CharField(max_length=300, blank=True)
    status         = models.CharField(max_length=30, choices=STATUS_CHOICES, default='enquiry')
    payment_method = models.CharField(max_length=20, blank=True, choices=PAYMENT_CHOICES)
    sales_order    = models.CharField(max_length=5, blank=True)
    project_number = models.PositiveIntegerField(null=True, blank=True, unique=True, db_index=True)
    drawing_number = models.CharField(max_length=100, blank=True)

    delivery_required = models.BooleanField(default=False)
    delivery_date     = models.DateField(null=True, blank=True)
    delivery_booked   = models.BooleanField(default=False)

    installation_required         = models.BooleanField(default=False)
    installation_same_as_delivery = models.BooleanField(default=False)
    installation_date_type        = models.CharField(max_length=10, choices=INSTALL_DATE_TYPE, default='exact')
    installation_date             = models.DateField(null=True, blank=True)
    installation_month            = models.CharField(max_length=7, blank=True)
    installation_month_part       = models.CharField(max_length=5, choices=MONTH_PART, blank=True, default='')
    installation_booked           = models.BooleanField(default=False)

    rams_required = models.BooleanField(default=False)
    rams_sent     = models.BooleanField(default=False)

    assigned_to    = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='assigned_projects')
    notes          = models.TextField(blank=True)
    created_by     = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_projects')
    addr_line1    = models.CharField(max_length=200, blank=True)
    addr_line2    = models.CharField(max_length=200, blank=True)
    addr_city     = models.CharField(max_length=100, blank=True)
    addr_county   = models.CharField(max_length=100, blank=True)
    addr_postcode = models.CharField(max_length=20, blank=True)
    addr_country  = models.CharField(max_length=60, blank=True, default='United Kingdom')
    addr_fao      = models.CharField(max_length=200, blank=True)
    addr_phone    = models.CharField(max_length=30, blank=True)
    last_edited_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='edited_projects')
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        # Keep the legacy customer text field in sync with the real link,
        # so every existing template/report/search that reads .customer as
        # text keeps working unchanged.
        if self.customer_profile_id and self.customer_profile.name:
            self.customer = self.customer_profile.name
        super().save(*args, **kwargs)

    def __str__(self):
        return self.project_name

    def get_effective_installation_date(self):
        if not self.installation_required:
            return None
        if self.installation_same_as_delivery:
            return self.delivery_date
        if self.installation_date_type == 'exact':
            return self.installation_date
        if self.installation_date_type == 'month' and self.installation_month:
            import calendar
            from datetime import date
            y, m = map(int, self.installation_month.split('-'))
            if self.installation_month_part == 'mid':
                return date(y, m, 15)
            elif self.installation_month_part == 'end':
                return date(y, m, calendar.monthrange(y, m)[1])
            else:
                return date(y, m, 1)
        return None

    ORDER_STATUSES = ('order_received', 'processed', 'part_delivered', 'completed')

    @property
    def is_order(self):
        """True once the project has progressed to Order Received or later
        (including completed). Cancelled never counts as an order."""
        return self.status in self.ORDER_STATUSES

    @property
    def accepted_cost(self):
        """Return the accepted costing option, or None."""
        return self.costs.filter(is_accepted=True).first()

    @property
    def cost(self):
        """Backward-compat: return the first/accepted cost option.
        Replaces the old OneToOne accessor."""
        return self.accepted_cost or self.costs.first()

    def get_traffic_light(self):
        from datetime import date
        if self.status in ('completed', 'on_hold', 'cancelled'):
            return 'done'
        d = self.get_effective_installation_date()
        if not d:
            d = self.delivery_date if self.delivery_required else None
        if not d:
            return 'tbc'
        days = (d - date.today()).days
        if days <= 15:
            return 'red'
        elif days <= 30:
            return 'yellow'
        return 'green'

    def is_overdue(self):
        from datetime import date
        if self.status in ('completed', 'on_hold', 'cancelled'):
            return False
        today = date.today()
        if self.delivery_required and self.delivery_date and self.delivery_date < today:
            return True
        inst = self.get_effective_installation_date()
        if inst and inst < today:
            return True
        return False


class ProjectLog(models.Model):
    project   = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='logs')
    user      = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    field     = models.CharField(max_length=100)
    old_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.project} — {self.field}"


class Comment(models.Model):
    project   = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='comments')
    user      = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    text      = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['timestamp']

    def __str__(self):
        return f"{self.project} — comment by {self.user}"


class Message(models.Model):
    sender    = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_messages')
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_messages')
    text      = models.TextField()
    read      = models.BooleanField(default=False)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['timestamp']

    def __str__(self):
        return f"{self.sender} → {self.recipient}: {self.text[:40]}"


class Notification(models.Model):
    TYPES = [
        ('tag',      'Tagged in comment'),
        ('message',  'New message'),
        ('reminder', 'Reminder'),
    ]
    user      = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    type      = models.CharField(max_length=20, choices=TYPES)
    text      = models.CharField(max_length=300)
    link      = models.CharField(max_length=200, blank=True)
    read      = models.BooleanField(default=False)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']


class Reminder(models.Model):
    project     = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='reminders')
    notify_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reminders')
    created_by  = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='reminders_created')
    message     = models.CharField(max_length=300)
    remind_at   = models.DateTimeField()
    sent        = models.BooleanField(default=False)
    dismissed   = models.BooleanField(default=False)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['remind_at']

    def __str__(self):
        return f"Reminder for {self.notify_user} at {self.remind_at}"


class TeamMessage(models.Model):
    user      = models.ForeignKey(User, on_delete=models.CASCADE, related_name='team_messages')
    text      = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['timestamp']

    def __str__(self):
        return f"{self.user}: {self.text[:40]}"


class StaffProfile(models.Model):
    COLOUR_CHOICES = [
        ('#d4700a', 'Orange'),
        ('#2563eb', 'Blue'),
        ('#16a34a', 'Green'),
        ('#9333ea', 'Purple'),
        ('#dc2626', 'Red'),
        ('#0891b2', 'Cyan'),
        ('#ca8a04', 'Yellow'),
        ('#be185d', 'Pink'),
        ('#0f766e', 'Teal'),
        ('#1d4ed8', 'Dark Blue'),
        ('#7c3aed', 'Violet'),
        ('#b45309', 'Brown'),
        ('#374151', 'Slate'),
        ('#db2777', 'Fuchsia'),
        ('#059669', 'Emerald'),
        ('#9f1239', 'Rose'),
    ]
    user   = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role   = models.CharField(max_length=100, blank=True)
    phone  = models.CharField(max_length=20, blank=True)
    bio    = models.TextField(blank=True)
    colour = models.CharField(max_length=7, blank=True, help_text='Hex colour for avatar')
    can_view_reports = models.BooleanField(default=False, help_text='Can view Sales Summary and Daily Accounts Report, independent of Staff status')
    THEME_CHOICES = [
        ('default', 'Default'),
        ('works_order', 'Works Order'),
        ('site_signage', 'Site Signage'),
        ('ledger', 'Ledger'),
    ]
    theme = models.CharField(max_length=20, choices=THEME_CHOICES, default='default', help_text='Personal visual style — set on your own profile')

    def __str__(self):
        return f"{self.user.get_full_name()} — {self.role}"


class LeaveRequest(models.Model):
    HALF_CHOICES = [
        ('full',      'Full Day'),
        ('morning',   'Morning'),
        ('afternoon', 'Afternoon'),
    ]
    user      = models.ForeignKey(User, on_delete=models.CASCADE, related_name='leave_requests')
    date      = models.DateField()
    half_day  = models.CharField(max_length=10, choices=HALF_CHOICES, default='full')
    note      = models.CharField(max_length=200, blank=True)
    added_by  = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='leave_added')
    created_at= models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date']
        unique_together = [['user', 'date', 'half_day']]

    def __str__(self):
        return f"{self.user.get_full_name()} — {self.date} ({self.half_day})"


class InstallationReport(models.Model):
    project        = models.OneToOneField(Project, on_delete=models.CASCADE, related_name='install_report')
    notes          = models.TextField(blank=True)
    issues         = models.TextField(blank=True)
    job_tasks      = models.TextField(blank=True, help_text='Schedule of works / job tasks for satisfaction note')
    fitting_crew        = models.CharField(max_length=300, blank=True)
    fitting_crew_phone  = models.CharField(max_length=100, blank=True)
    site_contact   = models.CharField(max_length=200, blank=True)
    contact_number = models.CharField(max_length=50, blank=True)
    site_cleared   = models.BooleanField(null=True, blank=True)
    site_cleared_notes = models.TextField(blank=True)
    work_completed = models.BooleanField(null=True, blank=True)
    work_completed_notes = models.TextField(blank=True)
    return_visit_required = models.BooleanField(null=True, blank=True)
    created_by     = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Report — {self.project}"


class ReportPhoto(models.Model):
    report    = models.ForeignKey(InstallationReport, on_delete=models.CASCADE, related_name='photos')
    image     = models.ImageField(upload_to='report_photos/', null=True, blank=True)
    file_data = models.BinaryField(null=True, blank=True)
    file_mime = models.CharField(max_length=100, blank=True)
    file_original_name = models.CharField(max_length=200, blank=True)
    caption   = models.CharField(max_length=200, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)


class SatisfactionNote(models.Model):
    report    = models.ForeignKey(InstallationReport, on_delete=models.CASCADE, related_name='satisfaction_notes')
    file      = models.FileField(upload_to='satisfaction_notes/', null=True, blank=True)
    file_data = models.BinaryField(null=True, blank=True)
    file_mime = models.CharField(max_length=100, blank=True)
    file_original_name = models.CharField(max_length=200, blank=True)
    caption   = models.CharField(max_length=200, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    @property
    def is_pdf(self):
        return (self.file_mime or '').lower() == 'application/pdf' or (self.file_original_name or '').lower().endswith('.pdf')


class CustomerProfile(models.Model):
    name         = models.CharField(max_length=200, help_text='Not required to be unique — imports may intentionally create duplicates to be reconciled by hand')
    is_active    = models.BooleanField(default=True)
    account_number = models.CharField(max_length=100, blank=True, help_text='Sage customer account reference')
    contact_name = models.CharField(max_length=200, blank=True)
    email        = models.EmailField(blank=True)
    email2       = models.EmailField(blank=True)
    email3       = models.EmailField(blank=True)
    phone        = models.CharField(max_length=30, blank=True)
    vat_number   = models.CharField(max_length=40, blank=True)
    company_reg_number = models.CharField(max_length=40, blank=True)
    eori_number  = models.CharField(max_length=40, blank=True)
    address_line1  = models.CharField(max_length=200, blank=True)
    address_line2  = models.CharField(max_length=200, blank=True)
    town           = models.CharField(max_length=100, blank=True)
    county         = models.CharField(max_length=100, blank=True)
    postcode       = models.CharField(max_length=20, blank=True)
    country        = models.CharField(max_length=60, blank=True, default='United Kingdom')
    address      = models.TextField(blank=True)
    notes            = models.TextField(blank=True)
    important_notes  = models.TextField(blank=True)
    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Supplier(models.Model):
    name           = models.CharField(max_length=200, unique=True)
    contact_name   = models.CharField(max_length=200, blank=True)
    email          = models.EmailField(blank=True)
    phone          = models.CharField(max_length=30, blank=True)
    address_line1  = models.CharField(max_length=200, blank=True)
    address_line2  = models.CharField(max_length=200, blank=True)
    town           = models.CharField(max_length=100, blank=True)
    county         = models.CharField(max_length=100, blank=True)
    postcode       = models.CharField(max_length=20, blank=True)
    address        = models.TextField(blank=True)   # legacy / combined
    account_number = models.CharField(max_length=100, blank=True)
    payment_terms  = models.CharField(max_length=100, blank=True)   # e.g. "30 days"
    lead_time_days = models.PositiveIntegerField(default=0)
    notes          = models.TextField(blank=True)
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def save(self, *args, **kwargs):
        if not self.account_number and self.name:
            # First 4 letters (A-Z only) uppercased + sequential 3-digit suffix
            prefix = ''.join(c for c in self.name.upper() if c.isalpha())[:4]
            if not prefix:
                prefix = 'XXXX'
            existing = Supplier.objects.filter(account_number__startswith=prefix)
            if self.pk:
                existing = existing.exclude(pk=self.pk)
            n = 1
            used = set()
            for s in existing:
                suffix = s.account_number[len(prefix):]
                if suffix.isdigit():
                    used.add(int(suffix))
            while n in used:
                n += 1
            self.account_number = f'{prefix}{n:03d}'
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class ProjectDocument(models.Model):
    DOC_TYPES = [
        ('drawing',      'Drawing'),
        ('quote',        'Quote'),
        ('contract',     'Contract'),
        ('rams',         'RAMS'),
        ('photo',        'Photo'),
        ('other',        'Other'),
    ]
    project      = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='documents')
    file         = models.FileField(upload_to='project_docs/', null=True, blank=True)
    # DB storage for production (no filesystem needed)
    file_data    = models.BinaryField(null=True, blank=True)
    file_mime    = models.CharField(max_length=100, blank=True)
    file_original_name = models.CharField(max_length=200, blank=True)
    name         = models.CharField(max_length=200)
    doc_type     = models.CharField(max_length=20, choices=DOC_TYPES, default='other')
    uploaded_by  = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    uploaded_at  = models.DateTimeField(auto_now_add=True)
    notes        = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return f"{self.name} — {self.project}"


class SupplierDocument(models.Model):
    DOC_TYPES = [
        ('price_list', 'Price List'),
        ('brochure',   'Brochure'),
        ('other',      'Other'),
    ]
    supplier     = models.ForeignKey('Supplier', on_delete=models.CASCADE, related_name='documents')
    file_data    = models.BinaryField(null=True, blank=True)
    file_mime    = models.CharField(max_length=100, blank=True)
    file_original_name = models.CharField(max_length=200, blank=True)
    name         = models.CharField(max_length=200)
    doc_type     = models.CharField(max_length=20, choices=DOC_TYPES, default='price_list')
    year         = models.PositiveIntegerField(null=True, blank=True, help_text='Year this price list/brochure applies to, if relevant')
    uploaded_by  = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    uploaded_at  = models.DateTimeField(auto_now_add=True)
    notes        = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return f"{self.name} — {self.supplier}"


class Product(models.Model):
    CATEGORY_CHOICES = [
        ('1', '1 Discontinued'),
        ('2', '2 Board'),
        ('3', '3 Type 1'),
        ('4', '4 Trimline'),
        ('5', '5 Type 1 & Trimline common'),
        ('6', '6 AR Longspan'),
        ('7', '7 AR Pallet Rack'),
        ('8', '8 Misc Fixings'),
        ('9', '9 JWW products'),
        ('10', '10 Wire & Mesh'),
        ('11', '11 Misc Metal'),
        ('12', '12 Ladders & Steps'),
        ('13', '13 Lockers & Cupboards'),
        ('14', '14 Box & Bin - Plastic'),
        ('15', '15 Box & Bin - Cardboard'),
        ('16', '16 Misc Out Sourced'),
        ('17', '17 Misc Non Product Item'),
        ('18', '18 Written Off'),
    ]
    is_active          = models.BooleanField(default=True)
    code               = models.CharField(max_length=100, unique=True)
    description        = models.CharField(max_length=300)
    category           = models.CharField(max_length=2, choices=CATEGORY_CHOICES, blank=True)
    quantity           = models.DecimalField(max_digits=10, decimal_places=2, default=0)  # In Stock
    qty_allocated      = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    qty_on_order       = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    reorder_level      = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    reorder_qty        = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    free_stock         = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    sales_price        = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    cost_price         = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    weight             = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, help_text='kg, used for delivery weight estimates')
    preferred_supplier = models.ForeignKey('Supplier', null=True, blank=True, on_delete=models.SET_NULL, related_name='products')
    updated_at         = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f"{self.code} — {self.description}"


class PickingList(models.Model):
    STATUS_CHOICES = [
        ('draft',      'Draft'),
        ('ready',      'Ready to Pick'),
        ('picked',     'Picked'),
        ('dispatched', 'Dispatched'),
    ]
    project    = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='picking_lists')
    status     = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    notes      = models.TextField(blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='picking_lists_created')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    allocated     = models.BooleanField(default=False)
    allocated_at  = models.DateTimeField(null=True, blank=True)
    allocated_by  = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='picking_lists_allocated')
    templates_used = models.ManyToManyField('PickingTemplate', blank=True, related_name='used_in_picking_lists')

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"PL-{self.pk} — {self.project}"


class PickingListItem(models.Model):
    ITEM_TYPES = [
        ('stock',   'Stock Item'),
        ('ns',      'Non-Stock Item'),
        ('message', 'Message / Note'),
    ]
    picking_list = models.ForeignKey(PickingList, on_delete=models.CASCADE, related_name='items')
    item_type    = models.CharField(max_length=10, choices=ITEM_TYPES, default='stock')
    product      = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='picking_items', null=True, blank=True)
    ns_description = models.CharField(max_length=300, blank=True)  # for NS items
    message      = models.CharField(max_length=500, blank=True)    # for message lines
    quantity     = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    picked       = models.BooleanField(default=False)
    sort_order   = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'id']

    def __str__(self):
        if self.item_type == 'stock':
            return f"{self.product.code} x{self.quantity}"
        elif self.item_type == 'ns':
            return f"NS: {self.ns_description} x{self.quantity}"
        return f"MSG: {self.message}"


class PickingTemplate(models.Model):
    name       = models.CharField(max_length=200)
    customer   = models.CharField(max_length=200, blank=True)
    notes      = models.TextField(blank=True)
    price      = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Set manually — templates often include bespoke items, so this is not auto-summed from Stock prices')
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class PickingTemplateItem(models.Model):
    ITEM_TYPES = [
        ('stock',   'Stock Item'),
        ('ns',      'Non-Stock Item'),
        ('message', 'Message / Note'),
    ]
    template      = models.ForeignKey(PickingTemplate, on_delete=models.CASCADE, related_name='items')
    item_type     = models.CharField(max_length=10, choices=ITEM_TYPES, default='stock')
    product       = models.ForeignKey(Product, on_delete=models.PROTECT, null=True, blank=True, related_name='template_items')
    ns_description= models.CharField(max_length=300, blank=True)
    message       = models.CharField(max_length=500, blank=True)
    quantity      = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    sort_order    = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'id']


class MaterialPrice(models.Model):
    """Configurable material prices (chipboard, melamine per sq ft)"""
    name       = models.CharField(max_length=100, unique=True)
    price_per_sqft = models.DecimalField(max_digits=8, decimal_places=4)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)

    def __str__(self):
        return f"{self.name}: £{self.price_per_sqft}/sqft"


class FittingCrew(models.Model):
    name  = models.CharField(max_length=200)
    phone = models.CharField(max_length=50, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} {self.phone}".strip()


class UprightAccessory(models.Model):
    """Per-upright accessories (e.g. SM footplates, top caps) that auto-calculate from frame counts."""
    name        = models.CharField(max_length=100)
    code        = models.CharField(max_length=50, blank=True)
    unit_price  = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    is_active   = models.BooleanField(default=True)
    sort_order  = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name


class AccessoryOverride(models.Model):
    cost        = models.ForeignKey('ProjectCost', on_delete=models.CASCADE, related_name='acc_overrides')
    accessory   = models.ForeignKey('UprightAccessory', on_delete=models.CASCADE)
    override_qty = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        unique_together = ('cost', 'accessory')


class ProjectCost(models.Model):
    project     = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='costs')
    label       = models.CharField(max_length=100, default='Option A')
    is_accepted = models.BooleanField(default=False)
    order       = models.PositiveSmallIntegerField(default=0)
    wall_fixings         = models.PositiveIntegerField(default=0)
    back_to_back_fixings = models.PositiveIntegerField(default=0)
    mobile_base_sets     = models.PositiveIntegerField(default=0)
    markup      = models.DecimalField(max_digits=6, decimal_places=2, default=50)
    labour      = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    delivery    = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    notes       = models.TextField(blank=True)
    updated_at  = models.DateTimeField(auto_now=True)
    updated_by  = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    accessories = models.ManyToManyField('UprightAccessory', blank=True)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return f"{self.label} — {self.project}"


class ProjectCostLine(models.Model):
    LINE_TYPES = [
        ('frame',   'Frame'),
        ('shelf',   'Shelf'),
        ('stock',   'Stock Item'),
        ('ns',      'Non-Stock'),
        ('inhang',  'In. Hanging'),
        ('outhang', 'Out. Hanging'),
        ('wipe',    'Wipe Board'),
        ('mesh',    'Mesh Panel'),
        ('loadsign','Load Sign'),
        ('toptie',  'Top Tie'),
        ('beams',   'Beams'),
        ('extras',  'Extras'),
        ('ls_frame','LS Frame'),
        ('ls_shelf','LS Shelf'),
        ('ls_trolley','LS Trolley'),
        ('template', 'Picking Template'),
    ]
    PRODUCT_LINES = [
        ('trimline', 'Trimline'),
        ('longspan', 'Longspan'),
    ]
    cost       = models.ForeignKey(ProjectCost, on_delete=models.CASCADE, related_name='lines')
    line_type  = models.CharField(max_length=12, choices=LINE_TYPES)
    product_line = models.CharField(max_length=10, choices=PRODUCT_LINES, default='trimline')
    description= models.CharField(max_length=300)
    # Frame/shelf specifics
    size       = models.CharField(max_length=50, blank=True)   # e.g. '120" x 24"'
    melamine   = models.BooleanField(default=False)             # shelf: chipboard or melamine
    no_deck    = models.BooleanField(default=False)             # shelf: no board at all — mesh/FR MDF/steel deck to be sourced separately
    # Stock link
    product    = models.ForeignKey(Product, null=True, blank=True, on_delete=models.SET_NULL)
    # Picking Template link — set when line_type='template', used to expand
    # into real picking-list items when the picking list is generated.
    picking_template = models.ForeignKey('PickingTemplate', null=True, blank=True, on_delete=models.SET_NULL, related_name='cost_lines')
    # Pricing
    quantity   = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    unit_cost  = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'id']

    @property
    def line_total(self):
        return float(self.quantity) * float(self.unit_cost)


class PurchaseOrder(models.Model):
    STATUS_CHOICES = [
        ('draft',     'Draft'),
        ('sent',      'Sent'),
        ('confirmed', 'Confirmed'),
        ('part_received', 'Partially Received'),
        ('received',  'Received'),
        ('cancelled', 'Cancelled'),
    ]
    po_number      = models.CharField(max_length=30, unique=True, blank=True)
    supplier       = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name='purchase_orders')
    project        = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL, related_name='purchase_orders')
    status         = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    order_date     = models.DateField(null=True, blank=True)
    expected_date  = models.DateField(null=True, blank=True)  # "Required Delivery" — what we asked for
    quote_number   = models.CharField(max_length=100, blank=True)  # supplier's quote ref, shown on printout
    acknowledged_date = models.DateField(null=True, blank=True)    # "Expected Delivery" — supplier-confirmed
    ack_reference  = models.CharField(max_length=100, blank=True)  # supplier's acknowledgement/sales order number
    notes          = models.TextField(blank=True)
    delivery_address  = models.TextField(blank=True)  # legacy, kept for compatibility
    del_company       = models.CharField(max_length=200, blank=True)
    del_line1         = models.CharField(max_length=200, blank=True)
    del_line2         = models.CharField(max_length=200, blank=True)
    del_city          = models.CharField(max_length=100, blank=True)
    del_county        = models.CharField(max_length=100, blank=True)
    del_postcode      = models.CharField(max_length=20, blank=True)
    del_contact       = models.CharField(max_length=200, blank=True)
    carriage          = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    locked         = models.BooleanField(default=False)
    locked_at      = models.DateTimeField(null=True, blank=True)
    locked_by      = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='pos_locked')
    received       = models.BooleanField(default=False)
    received_at    = models.DateTimeField(null=True, blank=True)
    received_by    = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='pos_received')
    status_before_receive = models.CharField(max_length=20, blank=True)  # for undo
    created_by     = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='pos_created')
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.po_number} — {self.supplier.name}"

    def save(self, *args, **kwargs):
        if not self.po_number:
            # PO-0001 style, based on max existing
            last = PurchaseOrder.objects.exclude(po_number='').order_by('-id').first()
            n = 1
            if last and last.po_number.startswith('PO-'):
                try:
                    n = int(last.po_number.split('-')[1]) + 1
                except (ValueError, IndexError):
                    n = PurchaseOrder.objects.count() + 1
            self.po_number = f'PO-{n:04d}'
        super().save(*args, **kwargs)

    @property
    def lines_total(self):
        return sum(line.line_total for line in self.lines.all())

    @property
    def total(self):
        return round(self.lines_total + float(self.carriage), 2)

    @property
    def total_with_vat(self):
        return round(self.total * 1.2, 2)


class PurchaseOrderLine(models.Model):
    LINE_TYPES = [
        ('stock',   'Stock'),
        ('ns',      'Non-Stock'),
        ('message', 'Message'),
    ]
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='lines')
    item_type      = models.CharField(max_length=10, choices=LINE_TYPES, default='stock')
    product        = models.ForeignKey(Product, null=True, blank=True, on_delete=models.SET_NULL)
    description    = models.CharField(max_length=300, blank=True)   # for non-stock / message
    quantity       = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    qty_received   = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    unit_cost      = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    sort_order     = models.PositiveIntegerField(default=0)

    @property
    def qty_outstanding(self):
        return max(float(self.quantity) - float(self.qty_received), 0)

    @property
    def is_fully_received(self):
        if self.item_type == 'message':
            return True
        return float(self.qty_received) >= float(self.quantity)

    class Meta:
        ordering = ['sort_order', 'id']

    @property
    def line_total(self):
        if self.item_type == 'message':
            return 0
        return round(float(self.quantity) * float(self.unit_cost), 2)

    @property
    def display_code(self):
        return self.product.code if self.product else ('' if self.item_type == 'message' else 'NS')

    @property
    def display_desc(self):
        return self.product.description if self.product else self.description


class ProductPriceChange(models.Model):
    product     = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='price_changes')
    old_price   = models.DecimalField(max_digits=10, decimal_places=2)
    new_price   = models.DecimalField(max_digits=10, decimal_places=2)
    supplier    = models.ForeignKey('Supplier', null=True, blank=True, on_delete=models.SET_NULL,
                   help_text='Preferred supplier at the time of this change, if set')
    changed_by  = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    changed_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-changed_at']

    def __str__(self):
        return f"{self.product.code}: £{self.old_price} → £{self.new_price}"


class StockMovement(models.Model):
    MOVEMENT_TYPES = [
        ('received',   'Received (PO)'),
        ('received_undo', 'Receipt Reversed'),
        ('allocated',  'Allocated'),
        ('deallocated','Allocation Released'),
        ('dispatched', 'Dispatched / Sent'),
        ('adjust',     'Manual Adjustment'),
    ]
    product        = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='movements')
    purchase_order = models.ForeignKey(PurchaseOrder, null=True, blank=True, on_delete=models.SET_NULL, related_name='movements')
    project        = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL, related_name='stock_movements')
    movement_type  = models.CharField(max_length=20, choices=MOVEMENT_TYPES, default='adjust')
    qty_change     = models.DecimalField(max_digits=10, decimal_places=2)  # + in, - out
    reason         = models.CharField(max_length=200, blank=True)
    user           = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def direction(self):
        return 'IN' if float(self.qty_change) >= 0 else 'OUT'

    def __str__(self):
        return f"{self.product.code} {self.qty_change:+} ({self.reason})"


class FittingNote(models.Model):
    title          = models.CharField(max_length=200)
    products       = models.ManyToManyField(Product, related_name='fitting_notes', blank=True)
    templates      = models.ManyToManyField(PickingTemplate, related_name='fitting_notes', blank=True)
    file_data      = models.BinaryField(null=True, blank=True)
    file_mime      = models.CharField(max_length=100, blank=True)
    file_original_name = models.CharField(max_length=200, blank=True)
    notes          = models.TextField(blank=True)
    uploaded_by    = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    uploaded_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['title']

    def __str__(self):
        return self.title

    @property
    def is_pdf(self):
        return (self.file_mime or '').lower() == 'application/pdf' or (self.file_original_name or '').lower().endswith('.pdf')

class QuotePhoto(models.Model):
    """A library photo for quotes, tagged with product codes. When a quote is
    generated, photos whose codes appear in the costing are auto-suggested."""
    title          = models.CharField(max_length=200)
    codes          = models.TextField(blank=True, help_text="Comma or space separated product codes")
    file_data      = models.BinaryField(null=True, blank=True)
    file_mime      = models.CharField(max_length=100, blank=True)
    file_original_name = models.CharField(max_length=200, blank=True)
    uploaded_by    = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    uploaded_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['title']

    def __str__(self):
        return self.title

    def code_list(self):
        import re
        return [c.strip().upper() for c in re.split(r'[,\s]+', self.codes or '') if c.strip()]


class ProjectQuote(models.Model):
    cost           = models.OneToOneField('ProjectCost', on_delete=models.CASCADE, related_name='quote', null=True, blank=True)
    project        = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='quotes', null=True, blank=True)
    photos         = models.ManyToManyField(QuotePhoto, blank=True, related_name='quotes')
    photo_columns  = models.PositiveSmallIntegerField(default=2)
    intro          = models.TextField(blank=True)
    greeting       = models.CharField(max_length=200, blank=True)
    thank_you      = models.TextField(blank=True)
    closing        = models.TextField(blank=True)
    signature_name = models.CharField(max_length=200, blank=True)
    quote_date     = models.CharField(max_length=100, blank=True)
    address_block  = models.TextField(blank=True)
    header_ref     = models.CharField(max_length=300, blank=True)
    supply_line    = models.TextField(blank=True)
    capacity       = models.TextField(blank=True)
    bay_breakdown  = models.TextField(blank=True)
    spec_note      = models.TextField(blank=True)
    main_price     = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    main_price_label = models.CharField(max_length=200, default='Our price to supply, deliver and install:')
    extra_label    = models.CharField(max_length=200, blank=True)
    extra_price    = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    lead_time      = models.CharField(max_length=200, blank=True)
    payment_terms  = models.CharField(max_length=200, blank=True)
    terms_text     = models.TextField(blank=True)
    updated_at     = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Quote — {self.project.project_name}"


class ProformaInvoice(models.Model):
    cost           = models.OneToOneField('ProjectCost', on_delete=models.CASCADE, related_name='proforma', null=True, blank=True)
    project        = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='proformas', null=True, blank=True)
    order_no       = models.CharField(max_length=100, blank=True)
    invoice_date   = models.CharField(max_length=100, blank=True)
    invoice_to     = models.TextField(blank=True)
    delivery_to    = models.CharField(max_length=300, blank=True, default='AS INVOICE')
    ezr_contact    = models.CharField(max_length=200, blank=True)
    po_number      = models.CharField(max_length=100, blank=True)
    requisitioner  = models.CharField(max_length=100, blank=True)
    shipped_via    = models.CharField(max_length=100, blank=True)
    fob_point      = models.CharField(max_length=100, blank=True)
    terms          = models.CharField(max_length=100, blank=True, default='PRO-FORMA')
    comments       = models.TextField(blank=True)
    description    = models.TextField(blank=True)
    goods_total    = models.DecimalField(max_digits=12, decimal_places=2, default=0)   # ex-VAT order value
    deposit_pct    = models.PositiveSmallIntegerField(default=50)
    vat_rate       = models.DecimalField(max_digits=5, decimal_places=2, default=20)
    shipping       = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    updated_at     = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Pro-Forma — {self.project.project_name}"


class PriceListItem(models.Model):
    """Editable price list for Trimline and Longspan items. Costings read from here."""
    CATEGORY_CHOICES = [
        ('ls_frame', 'Longspan Frame'),
        ('ls_shelf', 'Longspan Shelf Level'),
        ('ls_trolley', 'Longspan Trolley'),
        ('trimline', 'Trimline'),
        ('other', 'Other'),
    ]
    product_line = models.CharField(max_length=10, default='longspan')
    category     = models.CharField(max_length=60, choices=CATEGORY_CHOICES, default='ls_frame')
    code         = models.CharField(max_length=60, blank=True)   # e.g. size key '3000x900'
    label        = models.CharField(max_length=200)
    price        = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    weight       = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    sort_order   = models.PositiveIntegerField(default=0)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['product_line', 'category', 'sort_order', 'label']

    def __str__(self):
        return f"{self.label} — £{self.price}"

class QuoteAttachedPhoto(models.Model):
    """A photo manually uploaded to a specific quote, with size and order."""
    SIZE_CHOICES = [('small','Small'),('medium','Medium'),('large','Large')]
    quote        = models.ForeignKey(ProjectQuote, on_delete=models.CASCADE, related_name='attached_photos')
    file_data    = models.BinaryField(null=True, blank=True)
    file_mime    = models.CharField(max_length=100, blank=True)
    file_original_name = models.CharField(max_length=200, blank=True)
    size         = models.CharField(max_length=10, choices=SIZE_CHOICES, default='medium')
    sort_order   = models.PositiveIntegerField(default=0)
    uploaded_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sort_order', 'pk']

class ProjectPresence(models.Model):
    project   = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='presences')
    user      = models.ForeignKey(User, on_delete=models.CASCADE)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('project', 'user')

    def __str__(self):
        return f"{self.user} in {self.project}"


class DeliveryPhase(models.Model):
    """A delivery phase/drop for a project (e.g. Phase 1: ground floor)."""
    project      = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='delivery_phases')
    label        = models.CharField(max_length=200)              # e.g. "Phase 1 — Ground floor"
    delivered_on = models.DateField(null=True, blank=True)
    delivered    = models.BooleanField(default=False)
    notes        = models.TextField(blank=True)
    sort_order   = models.PositiveIntegerField(default=0)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sort_order', 'id']

    def __str__(self):
        return f"{self.project.project_name} — {self.label}"
