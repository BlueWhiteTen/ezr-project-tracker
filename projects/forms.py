from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from .models import Project


class RegisterForm(forms.Form):
    full_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={'class':'form-input','placeholder':'e.g. John Smith'}))
    email     = forms.EmailField(widget=forms.EmailInput(attrs={'class':'form-input','placeholder':'john@company.com'}))
    password1 = forms.CharField(widget=forms.PasswordInput(attrs={'class':'form-input','placeholder':'Password'}))
    password2 = forms.CharField(widget=forms.PasswordInput(attrs={'class':'form-input','placeholder':'Confirm password'}))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('password1') != cleaned.get('password2'):
            raise forms.ValidationError('Passwords do not match.')
        return cleaned

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip().lower()
        if not email.endswith('@ezrshelving.com'):
            raise forms.ValidationError('Only @ezrshelving.com email addresses can register.')
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError('An account with this email already exists.')
        return email

    def save(self):
        data = self.cleaned_data
        parts = data['full_name'].strip().split(' ', 1)
        first = parts[0]
        last  = parts[1] if len(parts) > 1 else ''
        username = data['email']
        # Create inactive — admin must approve before they can log in
        user = User.objects.create_user(
            username=username,
            email=data['email'],
            password=data['password1'],
            first_name=first,
            last_name=last,
            is_active=False,
        )
        return user


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = [
            'customer', 'location', 'status', 'sales_order', 'drawing_number', 'description',
            'assigned_to',
            'delivery_required', 'delivery_date', 'delivery_booked',
            'installation_required', 'installation_same_as_delivery',
            'installation_date_type', 'installation_date',
            'installation_month', 'installation_month_part', 'installation_booked',
            'rams_required', 'rams_sent',
            'notes',
        ]
        widgets = {
            'customer':        forms.TextInput(attrs={'class':'form-input','placeholder':'Customer name','id':'id_customer','autocomplete':'off'}),
            'location':        forms.TextInput(attrs={'class':'form-input','placeholder':'Site address or postcode'}),
            'status':          forms.Select(attrs={'class':'form-input'}),
            'sales_order':     forms.TextInput(attrs={'class':'form-input','placeholder':'10000','maxlength':'5','style':'font-family:monospace'}),
            'drawing_number':  forms.TextInput(attrs={'class':'form-input','placeholder':'e.g. DRW-2025-001'}),
            'description':     forms.TextInput(attrs={'class':'form-input','placeholder':'e.g. Remote stockroom, Ground floor, Phase 2…'}),
            'assigned_to':     forms.Select(attrs={'class':'form-input'}),
            'delivery_required':   forms.CheckboxInput(attrs={'class':'form-checkbox'}),
            'delivery_date':       forms.DateInput(attrs={'class':'form-input','type':'date'}),
            'delivery_booked':     forms.CheckboxInput(attrs={'class':'form-checkbox'}),
            'installation_required':          forms.CheckboxInput(attrs={'class':'form-checkbox'}),
            'installation_same_as_delivery':  forms.CheckboxInput(attrs={'class':'form-checkbox'}),
            'installation_date_type':         forms.RadioSelect(),
            'installation_date':              forms.DateInput(attrs={'class':'form-input','type':'date'}),
            'installation_month':             forms.TextInput(attrs={'class':'form-input','type':'month'}),
            'installation_month_part':        forms.Select(attrs={'class':'form-input'}),
            'installation_booked':            forms.CheckboxInput(attrs={'class':'form-checkbox'}),
            'rams_required': forms.CheckboxInput(attrs={'class':'form-checkbox'}),
            'rams_sent':     forms.CheckboxInput(attrs={'class':'form-checkbox'}),
            'notes':         forms.Textarea(attrs={'class':'form-input','rows':4,'placeholder':'Any additional notes…'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        qs = User.objects.filter(is_active=True).order_by('first_name','last_name')
        self.fields['assigned_to'].queryset = qs
        self.fields['assigned_to'].empty_label = '— Unassigned —'
        # Show full name if available, otherwise email prefix
        self.fields['assigned_to'].label_from_instance = lambda u: u.get_full_name() if u.get_full_name().strip() else u.email.split('@')[0].replace('.',' ').title()
        for f in ['delivery_date','installation_date','installation_month','assigned_to','location','sales_order','drawing_number','installation_month_part']:
            self.fields[f].required = False

    def clean(self):
        cleaned = super().clean()
        del_date  = cleaned.get('delivery_date')
        inst_date = cleaned.get('installation_date')
        same      = cleaned.get('installation_same_as_delivery')
        i_type    = cleaned.get('installation_date_type')
        if not same and i_type == 'exact' and del_date and inst_date:
            if inst_date < del_date:
                self.add_error('installation_date', 'Installation date cannot be before the delivery date.')
        return cleaned
