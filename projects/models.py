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
        ('completed',    'Completed'),
        ('on_hold',      'On Hold'),
        ('cancelled',    'Cancelled'),
    ]

    INSTALL_DATE_TYPE = [('exact','Exact Date'),('month','Month')]
    MONTH_PART        = [('','Any'),('early','Early'),('mid','Mid'),('end','End')]

    project_name   = models.CharField(max_length=200)
    customer       = models.CharField(max_length=200)
    location       = models.CharField(max_length=300, blank=True)
    description    = models.CharField(max_length=300, blank=True)
    status         = models.CharField(max_length=30, choices=STATUS_CHOICES, default='enquiry')
    sales_order    = models.CharField(max_length=5, blank=True)
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
    addr_postcode = models.CharField(max_length=20, blank=True)
    addr_fao      = models.CharField(max_length=200, blank=True)
    addr_phone    = models.CharField(max_length=30, blank=True)
    last_edited_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='edited_projects')
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

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

    def get_traffic_light(self):
        from datetime import date
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
        ('tag',     'Tagged in comment'),
        ('message', 'New message'),
    ]
    user      = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    type      = models.CharField(max_length=20, choices=TYPES)
    text      = models.CharField(max_length=300)
    link      = models.CharField(max_length=200, blank=True)
    read      = models.BooleanField(default=False)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']


class TeamMessage(models.Model):
    user      = models.ForeignKey(User, on_delete=models.CASCADE, related_name='team_messages')
    text      = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['timestamp']

    def __str__(self):
        return f"{self.user}: {self.text[:40]}"


class StaffProfile(models.Model):
    user  = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role  = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    bio   = models.TextField(blank=True)

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
    project    = models.OneToOneField(Project, on_delete=models.CASCADE, related_name='install_report')
    notes      = models.TextField(blank=True)
    issues     = models.TextField(blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Report — {self.project}"


class ReportPhoto(models.Model):
    report    = models.ForeignKey(InstallationReport, on_delete=models.CASCADE, related_name='photos')
    image     = models.ImageField(upload_to='report_photos/')
    caption   = models.CharField(max_length=200, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)


class SatisfactionNote(models.Model):
    report    = models.ForeignKey(InstallationReport, on_delete=models.CASCADE, related_name='satisfaction_notes')
    file      = models.FileField(upload_to='satisfaction_notes/')
    caption   = models.CharField(max_length=200, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)


class CustomerProfile(models.Model):
    name         = models.CharField(max_length=200, unique=True)
    contact_name = models.CharField(max_length=200, blank=True)
    email        = models.EmailField(blank=True)
    phone        = models.CharField(max_length=30, blank=True)
    address      = models.TextField(blank=True)
    notes            = models.TextField(blank=True)
    important_notes  = models.TextField(blank=True)
    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

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
    project     = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='documents')
    file        = models.FileField(upload_to='project_docs/')
    name        = models.CharField(max_length=200)
    doc_type    = models.CharField(max_length=20, choices=DOC_TYPES, default='other')
    uploaded_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    notes       = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return f"{self.name} — {self.project}"


class Product(models.Model):
    is_active          = models.BooleanField(default=True)
    code               = models.CharField(max_length=100, unique=True)
    description        = models.CharField(max_length=300)
    quantity           = models.DecimalField(max_digits=10, decimal_places=2, default=0)  # In Stock
    qty_allocated      = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    qty_on_order       = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    reorder_level      = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    reorder_qty        = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    free_stock         = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    sales_price        = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    cost_price         = models.DecimalField(max_digits=10, decimal_places=2, default=0)
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
    project     = models.OneToOneField(Project, on_delete=models.CASCADE, related_name='cost')
    wall_fixings = models.PositiveIntegerField(default=0)
    markup      = models.DecimalField(max_digits=6, decimal_places=2, default=50)
    labour      = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    delivery    = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    notes       = models.TextField(blank=True)
    updated_at  = models.DateTimeField(auto_now=True)
    updated_by  = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    accessories = models.ManyToManyField('UprightAccessory', blank=True)

    def __str__(self):
        return f"Cost for {self.project}"


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
    ]
    cost       = models.ForeignKey(ProjectCost, on_delete=models.CASCADE, related_name='lines')
    line_type  = models.CharField(max_length=10, choices=LINE_TYPES)
    description= models.CharField(max_length=300)
    # Frame/shelf specifics
    size       = models.CharField(max_length=50, blank=True)   # e.g. '120" x 24"'
    melamine   = models.BooleanField(default=False)             # shelf: chipboard or melamine
    # Stock link
    product    = models.ForeignKey(Product, null=True, blank=True, on_delete=models.SET_NULL)
    # Pricing
    quantity   = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    unit_cost  = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'id']

    @property
    def line_total(self):
        return float(self.quantity) * float(self.unit_cost)
