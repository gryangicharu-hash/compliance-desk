from django import forms

from .models import PRODUCTS, Channel, Level, MemberType, Pep

COUNTRIES = ["Kenya", "Uganda", "Tanzania", "Rwanda", "Burundi", "South Sudan", "Ethiopia", "Somalia",
             "United Kingdom", "United States", "United Arab Emirates", "Germany", "South Africa", "Other"]


class OnboardForm(forms.Form):
    member_type = forms.ChoiceField(choices=MemberType.choices)
    name = forms.CharField(max_length=200, label="Full name or registered name")
    id_number = forms.CharField(max_length=40, label="ID, passport or registration number")
    id_expiry = forms.DateField(required=False, label="ID expiry date", widget=forms.DateInput(attrs={"type": "date"}))
    date_of_birth = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    nationality = forms.ChoiceField(choices=[(c, c) for c in COUNTRIES], initial="Kenya")
    residence = forms.ChoiceField(choices=[(c, c) for c in COUNTRIES], initial="Kenya", label="Country of residence")
    occupation = forms.CharField(max_length=120, label="Occupation or business")
    employer = forms.CharField(max_length=160, required=False)
    checkoff = forms.BooleanField(required=False, label="Pays through check-off (salary deduction)")
    declared_monthly_income = forms.DecimalField(min_value=0, max_digits=18, decimal_places=2, label="Declared monthly income (KES)")
    source_of_funds = forms.CharField(max_length=500, required=False, widget=forms.Textarea(attrs={"rows": 2}))
    source_of_wealth = forms.CharField(max_length=500, required=False, widget=forms.Textarea(attrs={"rows": 2}),
                                       help_text="Required before a PEP can be approved.")
    channel = forms.ChoiceField(choices=Channel.choices, label="Onboarding channel")
    products = forms.MultipleChoiceField(choices=PRODUCTS, widget=forms.CheckboxSelectMultiple, initial=["bosa_deposits"])
    pep = forms.ChoiceField(choices=Pep.choices, label="PEP status")
    expected_cash = forms.ChoiceField(choices=Level.choices, label="Expected cash activity")
    kyc_complete = forms.BooleanField(required=False, label="All KYC documents received and checked")
    officials = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}),
                                label="Officials and signatories (groups and companies)",
                                help_text="One per line: name, role")
    beneficial_owners = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}),
                                        help_text="One per line: name, percentage held")

    def clean(self):
        data = super().clean()
        if data.get("member_type") and data["member_type"] != MemberType.INDIVIDUAL:
            if not (data.get("officials") or "").strip():
                self.add_error("officials", "Groups and companies need at least one official or signatory.")
        return data
