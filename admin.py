from django.contrib import admin
from .models import Project, ProjectLog, Customer

@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display  = ['project_name','customer','status','sales_order','delivery_date','installation_date','assigned_to']
    list_filter   = ['status','delivery_required','installation_required','rams_required','rams_sent']
    search_fields = ['project_name','customer','location','sales_order']

@admin.register(ProjectLog)
class ProjectLogAdmin(admin.ModelAdmin):
    list_display = ['project','user','field','old_value','new_value','timestamp']
    list_filter  = ['field']

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display  = ['name','created_at']
    search_fields = ['name']
