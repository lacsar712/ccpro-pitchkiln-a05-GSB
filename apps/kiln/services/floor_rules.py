"""灶台相位切换业务规则。"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

DRAWING_SOFT_POINT_MAX = Decimal("95")


def assert_can_enter_drawing(hearth) -> None:
    """
    进入「出胶」相位前：当前未收灶的 CookRun 须至少有一条
    softPointC <= 95 的 SoftPointProbe。
    """
    open_run = hearth.open_run()
    if open_run is None:
        raise ValidationError(
            {"phase": "无法进入出胶：该灶没有进行中的值守纪录。"}
        )

    ok = open_run.probes.filter(softPointC__lte=DRAWING_SOFT_POINT_MAX).exists()
    if not ok:
        raise ValidationError(
            {
                "phase": (
                    "无法进入出胶：进行中值守尚无软化点探针 "
                    f"≤ {DRAWING_SOFT_POINT_MAX}℃。"
                )
            }
        )


def on_duty_phases():
    """计入「在岗」的相位：仅升温、保温。出胶 / 冷灶 / 装料均不计入。"""
    from apps.kiln.models import FireHearth

    return (FireHearth.PHASE_RAMPING, FireHearth.PHASE_HOLDING)


def on_duty_count(lane: int) -> int:
    """实时在岗数：该过道当前处于升温或保温的灶台数。"""
    from apps.kiln.models import FireHearth

    return FireHearth.objects.filter(
        lane=lane, phase__in=on_duty_phases()
    ).count()


def latest_duty_card(lane: int, duty_date=None):
    """该过道该自然日最新一张夜班在岗卡；没有卡则返回 None。"""
    from apps.kiln.models import NightDutyCard

    if duty_date is None:
        duty_date = timezone.localdate()
    return (
        NightDutyCard.objects.filter(lane=lane, dutyDate=duty_date)
        .order_by("-id")
        .first()
    )


def gated_phase_steps():
    """受夜班在岗卡拦截的两步改相位：装料→升温、升温→保温。"""
    from apps.kiln.models import FireHearth

    return {
        (FireHearth.PHASE_CHARGING, FireHearth.PHASE_RAMPING),
        (FireHearth.PHASE_RAMPING, FireHearth.PHASE_HOLDING),
    }


def assert_duty_card_allows(hearth, new_phase: str) -> None:
    """
    装料→升温、升温→保温两步改相位前：
    先数该过道当前升温加保温灶（出胶与冷灶不计入），
    已达当日最新卡上限、或根本没有卡，则拒绝并提示先建卡。
    其余相位进出不拦截。
    """
    if (hearth.phase, new_phase) not in gated_phase_steps():
        return

    card = latest_duty_card(hearth.lane)
    count = on_duty_count(hearth.lane)
    if card is None:
        raise ValidationError(
            {
                "phase": (
                    f"过道 {hearth.lane} 今日尚无夜班在岗卡，"
                    "请先建卡再改相位。"
                )
            }
        )
    if count >= card.maxOnDuty:
        raise ValidationError(
            {
                "phase": (
                    f"过道 {hearth.lane} 当前在岗 {count} 灶，"
                    f"已达夜班在岗卡上限 {card.maxOnDuty}"
                    f"（{card.shiftName} · {card.supervisorName}），"
                    "请先建卡或调高上限。"
                )
            }
        )


def change_hearth_phase(hearth, new_phase: str):
    """统一入口：改相位时校验出胶规则与夜班在岗卡，并保存。"""
    from apps.kiln.models import FireHearth

    if new_phase == FireHearth.PHASE_DRAWING:
        assert_can_enter_drawing(hearth)
    assert_duty_card_allows(hearth, new_phase)

    hearth.phase = new_phase
    hearth.save(update_fields=["phase"])
    return hearth
