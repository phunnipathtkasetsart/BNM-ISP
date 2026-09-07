import re

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.core.validators import RegexValidator
from django.db import models

nisit_id_validator = RegexValidator(
    regex=r"^\d{10}$",
    message="Nisit ID must be exactly 10 digits, numbers only.",
)

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

    # Primary key mapped to PostgreSQL column 'userID'
    nisit_id = models.CharField(
        "Nisit ID",
        max_length=10,
        unique=True,
        db_column="userID",
        primary_key=True,
        validators=[nisit_id_validator],
    )
    first_name = models.CharField(max_length=150, db_column="userFirstName")
    last_name = models.CharField(max_length=150, db_column="userLastName")
    email = models.EmailField(
        "KU email", 
        unique=True, 
        db_column="userEmail", 
        validators=[ku_email_validator]
    )
    department = models.CharField(
        max_length=30, 
        choices=Department.choices, 
        db_column="userDepartment"
    )
    
    password = models.CharField(max_length=128, db_column="userPassword")

    is_active = models.BooleanField(default=True)
    is_superuser = models.BooleanField(default=False, db_column="isDepartment")
    is_staff = models.BooleanField(default=False, db_column="isLecturer")
    date_joined = models.DateTimeField(auto_now_add=True, db_column="createDate")

    last_login = models.DateTimeField(null=True, blank=True, db_column="last_login")

    objects = UserManager()

    USERNAME_FIELD = "nisit_id"
    REQUIRED_FIELDS = ["email", "first_name", "last_name", "department"]

    class Meta:
        db_table = 'ISP_DJANGO_2026"."Users'
        managed = False

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.nisit_id})"

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self):
        return self.first_name

    @property
    def is_department(self):
        return self.is_superuser

    @is_department.setter
    def is_department(self, value):
        self.is_superuser = value