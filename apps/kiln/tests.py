from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from .forms import NightDutyCardForm
from .models import FireHearth, NightDutyCard
from .services.floor_rules import change_hearth_phase, on_duty_hearth_count


class NightDutyGateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            "tester", password="secret123"
        )
        cls.today = timezone.localdate()

    def _hearth(self, tag, lane=3, phase=FireHearth.PHASE_CHARGING):
        return FireHearth.objects.create(
            lane=lane, tag=tag, resinGrade="特级脂", phase=phase
        )

    def _card(self, lane=3, cap=1):
        return NightDutyCard.objects.create(
            lane=lane,
            dutyDate=self.today,
            shiftName="夜班",
            maxActiveHearths=cap,
            supervisorName="岑守夜",
        )

    # —— 无卡必须拦 ——
    def test_no_card_blocks_ramping(self):
        h = self._hearth("灶-无卡")
        with self.assertRaises(ValidationError) as ctx:
            change_hearth_phase(h, FireHearth.PHASE_RAMPING)
        self.assertIn("先建卡", str(ctx.exception.message_dict["phase"][0]))
        h.refresh_from_db()
        self.assertEqual(h.phase, FireHearth.PHASE_CHARGING)

    def test_no_card_blocks_holding(self):
        h = self._hearth("灶-无卡保", phase=FireHearth.PHASE_RAMPING)
        with self.assertRaises(ValidationError):
            change_hearth_phase(h, FireHearth.PHASE_HOLDING)

    # —— 卡上限 ——
    def test_cap_one_blocks_second_active(self):
        self._card(cap=1)
        self._hearth("灶-甲", phase=FireHearth.PHASE_RAMPING)
        h2 = self._hearth("灶-乙")
        with self.assertRaises(ValidationError):
            change_hearth_phase(h2, FireHearth.PHASE_RAMPING)

    def test_under_cap_allows_ramping(self):
        self._card(cap=2)
        self._hearth("灶-甲", phase=FireHearth.PHASE_RAMPING)
        h2 = self._hearth("灶-乙")
        change_hearth_phase(h2, FireHearth.PHASE_RAMPING)
        self.assertEqual(h2.phase, FireHearth.PHASE_RAMPING)

    # —— 出胶、冷灶、装料不计在岗 ——
    def test_drawing_not_counted(self):
        self._card(cap=1)
        self._hearth("灶-出胶", phase=FireHearth.PHASE_DRAWING)
        self.assertEqual(on_duty_hearth_count(3), 0)
        h2 = self._hearth("灶-升温")
        change_hearth_phase(h2, FireHearth.PHASE_RAMPING)
        self.assertEqual(on_duty_hearth_count(3), 1)

    def test_cold_and_charging_not_counted(self):
        self._card(cap=1)
        self._hearth("灶-冷", phase=FireHearth.PHASE_COLD)
        self._hearth("灶-装", phase=FireHearth.PHASE_CHARGING)
        self.assertEqual(on_duty_hearth_count(3), 0)

    # —— 升温→保温：自身占额不重复计 ——
    def test_ramping_to_holding_replaces_self(self):
        self._card(cap=1)
        h = self._hearth("灶-自", phase=FireHearth.PHASE_RAMPING)
        # 自身已在岗，升温→保温不应被自己的名额挡住
        change_hearth_phase(h, FireHearth.PHASE_HOLDING)
        self.assertEqual(h.phase, FireHearth.PHASE_HOLDING)

    def test_ramping_to_holding_blocked_by_other(self):
        self._card(cap=1)
        self._hearth("灶-保温中", phase=FireHearth.PHASE_HOLDING)
        h2 = self._hearth("灶-升温中", phase=FireHearth.PHASE_RAMPING)
        with self.assertRaises(ValidationError):
            change_hearth_phase(h2, FireHearth.PHASE_HOLDING)

    # —— 改上限立即生效 ——
    def test_cap_change_takes_effect_immediately(self):
        card = self._card(cap=0)
        self._hearth("灶-占", phase=FireHearth.PHASE_HOLDING)
        h = self._hearth("灶-待")
        with self.assertRaises(ValidationError):
            change_hearth_phase(h, FireHearth.PHASE_RAMPING)
        card.maxActiveHearths = 2
        card.save(update_fields=["maxActiveHearths"])
        change_hearth_phase(h, FireHearth.PHASE_RAMPING)
        self.assertEqual(h.phase, FireHearth.PHASE_RAMPING)

    # —— 过道隔离 ——
    def test_lanes_isolated(self):
        self._card(lane=3, cap=1)
        h = self._hearth("灶-别过道", lane=4)
        with self.assertRaises(ValidationError):
            change_hearth_phase(h, FireHearth.PHASE_RAMPING)

    # —— 视图层：无卡时 POST 被拒并提示建卡 ——
    def test_view_blocks_without_card(self):
        h = self._hearth("灶-视图")
        self.client.force_login(self.user)
        resp = self.client.post(
            f"/hearth/{h.pk}/phase/",
            {"phase": FireHearth.PHASE_RAMPING},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "无夜班在岗卡")
        h.refresh_from_db()
        self.assertEqual(h.phase, FireHearth.PHASE_CHARGING)


class NightDutyCardFormTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()

    def _data(self, **overrides):
        data = {
            "lane": 1,
            "dutyDate": self.today.strftime("%Y-%m-%d"),
            "shiftName": "夜班",
            "maxActiveHearths": 1,
            "supervisorName": "岑守夜",
        }
        data.update(overrides)
        return data

    def test_duplicate_lane_date_shift_rejected(self):
        NightDutyCard.objects.create(
            lane=1,
            dutyDate=self.today,
            shiftName="夜班",
            maxActiveHearths=1,
            supervisorName="祁大山",
        )
        form = NightDutyCardForm(data=self._data())
        self.assertFalse(form.is_valid())
        self.assertIn("__all__", form.errors)

    def test_different_shift_allowed(self):
        NightDutyCard.objects.create(
            lane=1,
            dutyDate=self.today,
            shiftName="夜班",
            maxActiveHearths=1,
            supervisorName="祁大山",
        )
        form = NightDutyCardForm(data=self._data(shiftName="小夜班"))
        self.assertTrue(form.is_valid(), form.errors)


class NightDutyCardViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("tester", password="secret123")
        cls.today = timezone.localdate()

    def test_create_then_edit_cap_takes_effect(self):
        self.client.force_login(self.user)
        resp = self.client.post(
            "/night-duty/",
            {
                "lane": 5,
                "dutyDate": self.today.strftime("%Y-%m-%d"),
                "shiftName": "夜班",
                "maxActiveHearths": 0,
                "supervisorName": "苗晚晴",
            },
        )
        self.assertEqual(resp.status_code, 302)
        card = NightDutyCard.objects.get(lane=5)
        self.assertEqual(card.maxActiveHearths, 0)

        h = FireHearth.objects.create(
            lane=5,
            tag="灶-待升温",
            resinGrade="x",
            phase=FireHearth.PHASE_CHARGING,
        )
        # 上限 0：必须拦
        with self.assertRaises(ValidationError):
            change_hearth_phase(h, FireHearth.PHASE_RAMPING)

        # 改上限为 1 后下一笔改相位立即放行
        self.client.post(
            f"/night-duty/{card.pk}/",
            {
                "lane": 5,
                "dutyDate": self.today.strftime("%Y-%m-%d"),
                "shiftName": "夜班",
                "maxActiveHearths": 1,
                "supervisorName": "苗晚晴",
            },
        )
        card.refresh_from_db()
        self.assertEqual(card.maxActiveHearths, 1)
        change_hearth_phase(h, FireHearth.PHASE_RAMPING)
        self.assertEqual(h.phase, FireHearth.PHASE_RAMPING)
