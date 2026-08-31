from django import forms

from .models import User


class LoginForm(forms.Form):
    nisit_id = forms.CharField(
        label="Nisit ID",
        max_length=10,
        widget=forms.TextInput(attrs={"placeholder": "Nisit ID", "autofocus": True}),
    )
    password = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={"placeholder": "Password"}),
    )


class RegisterForm(forms.ModelForm):
    password1 = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={"placeholder": "Password"}),
        help_text="At least 8 characters.",
    )

    class Meta:
        model = User
        fields = ["first_name", "last_name", "nisit_id", "department", "email"]
        widgets = {
            "first_name": forms.TextInput(attrs={"placeholder": "First Name"}),
            "last_name": forms.TextInput(attrs={"placeholder": "Last Name"}),
            "nisit_id": forms.TextInput(
                attrs={"placeholder": "Nisit Number (Student ID) (max 10 digits)"}
            ),
            "department": forms.RadioSelect,
            "email": forms.EmailInput(attrs={"placeholder": "KU Email (@ku.th)"}),
        }
        labels = {"email": "KU Email"}

    def clean_email(self):
        email = self.cleaned_data.get("email", "")
        if not email.lower().endswith("@ku.th"):
            raise forms.ValidationError("Please use your KU email (must end with @ku.th).")
        return email

    def clean_password1(self):
        password = self.cleaned_data.get("password1", "")
        if len(password) < 8:
            raise forms.ValidationError("Password must be at least 8 characters.")
        return password

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user
