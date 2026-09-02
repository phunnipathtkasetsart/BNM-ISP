import re

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.core.validators import RegexValidator
from django.db import models


nisit_id_validator = RegexValidator(
    regex=r"^\d{10}$",
    message="Nisit ID must be exactly 10 digits, numbers only.",
)

# Anything before the @, then exactly ku.th. Note this rejects sub-domains
# such as name@eng.ku.th, which is what "must be a KU email" means here.
ku_email_validator = RegexValidator(
    regex=r"^[^@\s]+@ku\.th$",
    message="Enter your KU email — it must end with @ku.th.",
    flags=re.IGNORECASE,
)


class UserManager(BaseUserManager):
    """Custom manager since our USERNAME_FIELD is nisit_id, not username."""

    def create_user(self, nisit_id, email, password=None, **extra_fields):
        if not nisit_id:
            raise ValueError("Users must have a Nisit ID")
        if not email:
            raise ValueError("Users must have a KU email")

        email = self.normalize_email(email)
        user = self.model(nisit_id=nisit_id, email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, nisit_id, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        return self.create_user(nisit_id, email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    class Department(models.TextChoices):
        SKE = "ske", "Software & Knowledge Engineering"
        CPE = "cpe", "Computer Engineering"

    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    nisit_id = models.CharField(
        "Nisit ID",
        max_length=10,
        unique=True,
        validators=[nisit_id_validator],
    )
    department = models.CharField(max_length=10, choices=Department.choices)
    email = models.EmailField("KU email", unique=True, validators=[ku_email_validator])

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = "nisit_id"
    REQUIRED_FIELDS = ["email", "first_name", "last_name", "department"]

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.nisit_id})"

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self):
        return self.first_name
