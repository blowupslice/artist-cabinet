from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.forms import AuthenticationForm

from .importer import ImportError_, parse_report
from .models import Artist, Report

User = get_user_model()


class EmailLoginForm(AuthenticationForm):
    username = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email", "placeholder": "you@example.com"}),
    )
    password = forms.CharField(
        label="Пароль", strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )
    error_messages = {
        "invalid_login": "Неверный email или пароль.",
        "inactive": "Доступ к кабинету отключён. Свяжитесь с менеджером.",
    }

    def clean_username(self):
        return self.cleaned_data["username"].strip().lower()


class ArtistAdminForm(forms.ModelForm):
    """Форма артиста в админке: сразу создаёт/меняет аккаунт для входа."""

    email = forms.EmailField(label="Email (логин)")
    password = forms.CharField(
        label="Пароль", required=False, strip=False,
        widget=forms.TextInput(attrs={"autocomplete": "new-password"}),
        help_text="Для нового артиста — обязательно. Для существующего заполните, "
                  "только если хотите сменить пароль. Отправьте его артисту сами.",
    )
    is_active = forms.BooleanField(label="Доступ открыт", required=False, initial=True)

    class Meta:
        model = Artist
        fields = ["name", "legal_name", "phone", "notes"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["email"].initial = self.instance.user.email
            self.fields["is_active"].initial = self.instance.user.is_active

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        qs = User.objects.filter(email__iexact=email)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.user_id)
        existing = qs.first()
        self._reuse_user = None
        if existing:
            # Аккаунт без артиста (например, остался от неудачного сохранения) можно привязать заново
            if not existing.is_staff and not Artist.objects.filter(user=existing).exists():
                self._reuse_user = existing
            else:
                raise forms.ValidationError("Пользователь с таким email уже есть")
        return email

    def clean_password(self):
        pwd = self.cleaned_data.get("password")
        if not self.instance.pk and not pwd:
            raise forms.ValidationError("Задайте пароль для нового артиста")
        if pwd:
            password_validation.validate_password(pwd)
        return pwd

    def save(self, commit=True):
        """Аккаунт сохраняется только вместе с артистом (см. save_user), чтобы при
        ошибке в договорах/отчётах на той же странице не оставалось «пустых» аккаунтов."""
        artist = super().save(commit=False)
        if commit:
            self.save_user(artist)
            artist.save()
        return artist

    def save_user(self, artist):
        if artist.pk:
            user = artist.user
        else:
            user = getattr(self, "_reuse_user", None) or User()
            user.first_name = artist.name[:150]
        user.email = self.cleaned_data["email"]
        user.is_active = self.cleaned_data["is_active"]
        if self.cleaned_data.get("password"):
            user.set_password(self.cleaned_data["password"])
        user.save()
        artist.user = user
        return user


class ReportAdminForm(forms.ModelForm):
    """При загрузке детализации сразу разбираем файл и показываем ошибку, если формат не распознан."""

    class Meta:
        model = Report
        fields = "__all__"

    def clean_detail_file(self):
        f = self.cleaned_data.get("detail_file")
        if f and "detail_file" in self.changed_data:
            try:
                self.instance._parsed_rows = parse_report(f)
            except ImportError_ as exc:
                raise forms.ValidationError(str(exc)) from exc
        return f


class PasswordChangeFormRu(forms.Form):
    old_password = forms.CharField(label="Текущий пароль", widget=forms.PasswordInput)
    new_password1 = forms.CharField(label="Новый пароль", widget=forms.PasswordInput,
                                    help_text="Не короче 8 символов, не только цифры.")
    new_password2 = forms.CharField(label="Повторите новый пароль", widget=forms.PasswordInput)

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_old_password(self):
        pwd = self.cleaned_data["old_password"]
        if not self.user.check_password(pwd):
            raise forms.ValidationError("Текущий пароль указан неверно")
        return pwd

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("new_password1"), data.get("new_password2")
        if p1 and p2:
            if p1 != p2:
                self.add_error("new_password2", "Пароли не совпадают")
            else:
                try:
                    password_validation.validate_password(p1, self.user)
                except forms.ValidationError as exc:
                    self.add_error("new_password1", exc)
        return data

    def save(self):
        self.user.set_password(self.cleaned_data["new_password1"])
        self.user.save()
        return self.user
