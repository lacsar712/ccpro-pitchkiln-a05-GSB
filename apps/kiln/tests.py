from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from .models import FireHearth, NightDutyCard
from .seed import ensure_seed_data
from .services.floor_rules import (
    change_hearth_phase,
    latest_duty_card,
    on_duty_count,
)


def _hearth(lane, tag, phase):
    return FireHearth.objects.create(
        lane=lane, tag=tag, resinGrade="特级脂", phase=phase
    )


def _card(lane=1, max_on_duty=1, shift="夜班", duty_date=None):
    return NightDutyCard.objects.create(
        lane=lane,
        dutyDate=duty_date or timezone.localdate(),
        shiftName=shift,
        maxOnDuty=max_on_duty,
        supervisorName="祁师傅",
    )


class OnDutyCountTests(TestCase):
    """在岗计数：只数升温 + 保温；出胶、冷灶、装料不计入。"""

    def test_only_ramping_and_holding_count(self):
        _hearth(1, "试-升温", FireHearth.PHASE_RAMPING)
        _hearth(1, "试-保温", FireHearth.PHASE_HOLDING)
        _hearth(1, "试-出胶", FireHearth.PHASE_DRAWING)
        _hearth(1, "试-冷灶", FireHearth.PHASE_COLD)
        _hearth(1, "试-装料", FireHearth.PHASE_CHARGING)
        _hearth(2, "试-邻道升温", FireHearth.PHASE_RAMPING)
        self.assertEqual(on_duty_count(1), 2)


class DutyCardGateTests(TestCase):
    """装料→升温、升温→保温 两步受夜班在岗卡拦截。"""

    def test_no_card_blocks_charging_to_ramping(self):
        h = _hearth(1, "试-装料", FireHearth.PHASE_CHARGING)
        with self.assertRaises(ValidationError):
            change_hearth_phase(h, FireHearth.PHASE_RAMPING)
        h.refresh_from_db()
        self.assertEqual(h.phase, FireHearth.PHASE_CHARGING)

    def test_at_limit_blocks_ramping_to_holding(self):
        _card(max_on_duty=1)
        h = _hearth(1, "试-升温", FireHearth.PHASE_RAMPING)
        with self.assertRaises(ValidationError):
            change_hearth_phase(h, FireHearth.PHASE_HOLDING)
        h.refresh_from_db()
        self.assertEqual(h.phase, FireHearth.PHASE_RAMPING)

    def test_raising_limit_frees_next_transition(self):
        card = _card(max_on_duty=1)
        h = _hearth(1, "试-升温", FireHearth.PHASE_RAMPING)
        with self.assertRaises(ValidationError):
            change_hearth_phase(h, FireHearth.PHASE_HOLDING)
        card.maxOnDuty = 2
        card.save()
        change_hearth_phase(h, FireHearth.PHASE_HOLDING)
        h.refresh_from_db()
        self.assertEqual(h.phase, FireHearth.PHASE_HOLDING)

    def test_drawing_and_cold_not_counted(self):
        _card(max_on_duty=1)
        _hearth(1, "试-出胶", FireHearth.PHASE_DRAWING)
        _hearth(1, "试-冷灶", FireHearth.PHASE_COLD)
        h = _hearth(1, "试-装料", FireHearth.PHASE_CHARGING)
        change_hearth_phase(h, FireHearth.PHASE_RAMPING)
        h.refresh_from_db()
        self.assertEqual(h.phase, FireHearth.PHASE_RAMPING)

    def test_other_transitions_not_gated(self):
        h = _hearth(1, "试-冷灶", FireHearth.PHASE_COLD)
        change_hearth_phase(h, FireHearth.PHASE_CHARGING)
        h.refresh_from_db()
        self.assertEqual(h.phase, FireHearth.PHASE_CHARGING)

    def test_latest_card_wins(self):
        _card(max_on_duty=1, shift="夜班")
        _card(max_on_duty=3, shift="深夜班")
        self.assertEqual(latest_duty_card(1).maxOnDuty, 3)

    def test_unique_lane_date_shift(self):
        _card()
        with self.assertRaises(IntegrityError):
            _card()


class SeedDutyCardTests(TestCase):
    """种子：一过道今日卡上限 1，且该过道已有一灶升温。"""

    def test_seed_lane1_card_and_one_ramping(self):
        ensure_seed_data()
        card = NightDutyCard.objects.get(
            lane=1, dutyDate=timezone.localdate(), shiftName="夜班"
        )
        self.assertEqual(card.maxOnDuty, 1)
        self.assertEqual(on_duty_count(1), 1)
        self.assertTrue(
            FireHearth.objects.filter(
                lane=1, phase=FireHearth.PHASE_RAMPING
            ).exists()
        )
