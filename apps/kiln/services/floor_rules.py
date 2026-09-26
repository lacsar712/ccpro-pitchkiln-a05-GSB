"""灶台相位切换业务规则。"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

DRAWING_SOFT_POINT_MAX = Decimal("95")

# 在岗相位：只有「升温」「保温」计入夜班在岗；
# 装料（未点火升压）、出胶（已撤火）、冷灶均不计。
ON_DUTY_PHASES = (
    "ramping",  # PHASE_RAMPING
    "holding",  # PHASE_HOLDING
)


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


def latest_duty_card(lane: int, duty_date=None):
    """该过道、该自然日生效的夜班在岗卡（当日多班时取最新建的一张）。"""
    from apps.kiln.models import NightDutyCard

    if duty_date is None:
        duty_date = timezone.localdate()
    return (
        NightDutyCard.objects.filter(lane=lane, dutyDate=duty_date)
        .order_by("-id")
        .first()
    )


def on_duty_hearth_count(lane: int) -> int:
    """
    实时在岗灶数 = 该过道当前相位为「升温」或「保温」的灶台数。
    出胶灶、冷灶、装料灶一律不计入。看板显示与改相位拦截同源调用本函数。
    """
    from apps.kiln.models import FireHearth

    return FireHearth.objects.filter(lane=lane, phase__in=ON_DUTY_PHASES).count()


def lane_duty_status(lane: int, duty_date=None):
    """返回 (当日在岗卡, 实时在岗灶数)；无卡时首项为 None。"""
    if duty_date is None:
        duty_date = timezone.localdate()
    return latest_duty_card(lane, duty_date), on_duty_hearth_count(lane)


def assert_can_enter_on_duty(hearth) -> None:
    """
    进入在岗相位（装料→升温、升温→保温）前的夜班在岗卡校验：
    当日该过道无卡 → 拒绝并提示先建卡；在岗数已达卡上限 → 拒绝。
    灶台自身若已在升温/保温（如升温→保温），计数时扣除自身，不重复占额。
    """
    card = latest_duty_card(hearth.lane)
    if card is None:
        raise ValidationError(
            {
                "phase": (
                    f"过道 {hearth.lane} 今日尚无夜班在岗卡，"
                    "请先建卡（或联系值班主管建卡）后再改相位。"
                )
            }
        )

    active = on_duty_hearth_count(hearth.lane)
    if hearth.phase in ON_DUTY_PHASES:
        active -= 1
    if active >= card.maxActiveHearths:
        raise ValidationError(
            {
                "phase": (
                    f"过道 {hearth.lane} 在岗灶已达夜班在岗卡上限"
                    f"（{active}/{card.maxActiveHearths}，仅计升温+保温）："
                    f"{card.shiftName} · 值班主管 {card.supervisorName}。"
                    "如须增量请先改卡上限。"
                )
            }
        )


def change_hearth_phase(hearth, new_phase: str):
    """统一入口：改相位时校验出胶 / 夜班在岗规则并保存。"""
    from apps.kiln.models import FireHearth

    if new_phase == FireHearth.PHASE_DRAWING:
        assert_can_enter_drawing(hearth)

    if new_phase in ON_DUTY_PHASES:
        assert_can_enter_on_duty(hearth)

    hearth.phase = new_phase
    hearth.save(update_fields=["phase"])
    return hearth
