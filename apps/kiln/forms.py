from django import forms
from django.utils import timezone

from .models import (
    CookRun,
    FireHearth,
    NightDutyCard,
    ResinLot,
    SoftPointProbe,
)
from .services.floor_rules import assert_can_enter_drawing


class NightDutyCardForm(forms.ModelForm):
    class Meta:
        model = NightDutyCard
        fields = [
            "lane",
            "dutyDate",
            "shiftName",
            "maxActiveHearths",
            "supervisorName",
        ]
        widgets = {
            "lane": forms.NumberInput(attrs={"class": "field", "min": 1}),
            "dutyDate": forms.DateInput(
                attrs={"class": "field", "type": "date"}
            ),
            "shiftName": forms.TextInput(
                attrs={"class": "field", "placeholder": "如：夜班 / 小夜班"}
            ),
            "maxActiveHearths": forms.NumberInput(
                attrs={"class": "field", "min": 0, "step": 1}
            ),
            "supervisorName": forms.TextInput(attrs={"class": "field"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["dutyDate"].input_formats = ["%Y-%m-%d"]
        if not self.is_bound and not (self.instance and self.instance.pk):
            self.initial["dutyDate"] = timezone.localdate().strftime("%Y-%m-%d")
            self.initial["shiftName"] = "夜班"
            self.initial["maxActiveHearths"] = 1

    def clean(self):
        cleaned = super().clean()
        lane = cleaned.get("lane")
        duty_date = cleaned.get("dutyDate")
        shift_name = cleaned.get("shiftName")
        if lane is not None and duty_date is not None and shift_name:
            qs = NightDutyCard.objects.filter(
                lane=lane, dutyDate=duty_date, shiftName=shift_name
            )
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError(
                    "该过道 + 值班日 + 班次的在岗卡已存在，请直接改原卡上限。"
                )
        return cleaned


class ResinLotForm(forms.ModelForm):
    class Meta:
        model = ResinLot
        fields = ["lotCode", "originPlace", "arrivalKg", "receivedAt"]
        widgets = {
            "lotCode": forms.TextInput(attrs={"class": "field"}),
            "originPlace": forms.TextInput(attrs={"class": "field"}),
            "arrivalKg": forms.NumberInput(attrs={"class": "field", "step": "0.01"}),
            "receivedAt": forms.DateTimeInput(
                attrs={"class": "field", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["receivedAt"].input_formats = [
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
        ]
        if self.instance and self.instance.pk and self.instance.receivedAt:
            local = timezone.localtime(self.instance.receivedAt)
            self.initial["receivedAt"] = local.strftime("%Y-%m-%dT%H:%M")


class PhaseChangeForm(forms.Form):
    phase = forms.ChoiceField(
        label="相位",
        choices=FireHearth.PHASE_CHOICES,
        widget=forms.Select(attrs={"class": "field"}),
    )

    def __init__(self, *args, hearth=None, **kwargs):
        self.hearth = hearth
        super().__init__(*args, **kwargs)
        if hearth is not None and not self.is_bound:
            self.fields["phase"].initial = hearth.phase

    def clean_phase(self):
        phase = self.cleaned_data["phase"]
        if self.hearth is not None and phase == FireHearth.PHASE_DRAWING:
            assert_can_enter_drawing(self.hearth)
        return phase


class SoftPointProbeForm(forms.ModelForm):
    class Meta:
        model = SoftPointProbe
        fields = ["sampledAt", "softPointC", "samplerName"]
        widgets = {
            "sampledAt": forms.DateTimeInput(
                attrs={"class": "field", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "softPointC": forms.NumberInput(attrs={"class": "field", "step": "0.01"}),
            "samplerName": forms.TextInput(attrs={"class": "field"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sampledAt"].input_formats = [
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
        ]
        if not self.is_bound and not (self.instance and self.instance.pk):
            self.initial["sampledAt"] = timezone.localtime().strftime("%Y-%m-%dT%H:%M")


class OpenCookRunForm(forms.ModelForm):
    class Meta:
        model = CookRun
        fields = ["resinLot", "openedAt", "targetSoftPointC"]
        widgets = {
            "resinLot": forms.Select(attrs={"class": "field"}),
            "openedAt": forms.DateTimeInput(
                attrs={"class": "field", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "targetSoftPointC": forms.NumberInput(
                attrs={"class": "field", "step": "0.01"}
            ),
        }

    def __init__(self, *args, hearth=None, **kwargs):
        self.hearth = hearth
        super().__init__(*args, **kwargs)
        self.fields["openedAt"].input_formats = [
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
        ]
        self.fields["resinLot"].queryset = ResinLot.objects.all()
        if not self.is_bound:
            self.initial["openedAt"] = timezone.localtime().strftime("%Y-%m-%dT%H:%M")

    def clean(self):
        cleaned = super().clean()
        if self.hearth is not None and self.hearth.open_run() is not None:
            raise forms.ValidationError("该灶已有进行中的值守，请先收灶再开新灶。")
        return cleaned
