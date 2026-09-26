from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Prefetch
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .forms import (
    NightDutyCardForm,
    OpenCookRunForm,
    PhaseChangeForm,
    ResinLotForm,
    SoftPointProbeForm,
)
from .models import CookRun, FireHearth, NightDutyCard, ResinLot
from .services.floor_rules import (
    change_hearth_phase,
    lane_duty_status,
    on_duty_hearth_count,
)


def _wants_htmx(request):
    return request.headers.get("HX-Request") == "true"


def _hearths_for_board():
    return FireHearth.objects.prefetch_related(
        Prefetch(
            "runs",
            queryset=CookRun.objects.filter(closedAt__isnull=True)
            .select_related("resinLot")
            .prefetch_related("probes"),
            to_attr="open_runs_cache",
        )
    ).order_by("lane", "tag")


def _board_context():
    hearths = list(_hearths_for_board())
    lanes = {}
    for h in hearths:
        lanes.setdefault(h.lane, []).append(h)
    phase_legend = [
        (key, label, sum(1 for h in hearths if h.phase == key))
        for key, label in FireHearth.PHASE_CHOICES
    ]
    # 每条过道附带当日夜班在岗卡与实时在岗数
    # （在岗数与改相位拦截同源调用 on_duty_hearth_count）
    lane_rows = []
    for lane, tiles in sorted(lanes.items()):
        card, active = lane_duty_status(lane)
        lane_rows.append((lane, tiles, card, active))
    return {
        "hearths": hearths,
        "lanes": lane_rows,
        "phase_legend": phase_legend,
    }


def _drawer_context(hearth):
    open_run = hearth.open_run()
    probes = []
    if open_run:
        probes = list(open_run.probes.order_by("-sampledAt", "-id"))
    duty_card, duty_active = lane_duty_status(hearth.lane)
    return {
        "hearth": hearth,
        "open_run": open_run,
        "probes": probes,
        "duty_card": duty_card,
        "duty_active": duty_active,
        "phase_form": PhaseChangeForm(hearth=hearth),
        "probe_form": SoftPointProbeForm() if open_run else None,
        "open_run_form": OpenCookRunForm(hearth=hearth) if open_run is None else None,
    }


@login_required
def home(request):
    ctx = _board_context()
    drawer_pk = request.GET.get("hearth")
    if drawer_pk:
        try:
            hearth = FireHearth.objects.get(pk=drawer_pk)
            ctx.update(_drawer_context(hearth))
            ctx["drawer_open"] = True
        except (FireHearth.DoesNotExist, ValueError):
            ctx["drawer_open"] = False
    else:
        ctx["drawer_open"] = False
    return render(request, "floor/board.html", ctx)


@login_required
def floor_grid_partial(request):
    html = render_to_string("floor/_grid.html", _board_context(), request=request)
    return HttpResponse(html)


@login_required
def hearth_drawer(request, pk):
    hearth = get_object_or_404(FireHearth, pk=pk)
    ctx = _drawer_context(hearth)
    if _wants_htmx(request):
        return render(request, "floor/_drawer.html", ctx)
    return redirect(f"/?hearth={pk}")


@login_required
@require_POST
def change_phase(request, pk):
    hearth = get_object_or_404(FireHearth, pk=pk)
    form = PhaseChangeForm(request.POST, hearth=hearth)
    if form.is_valid():
        try:
            change_hearth_phase(hearth, form.cleaned_data["phase"])
            messages.success(request, f"灶牌 {hearth.tag} 相位已更新")
        except ValidationError as exc:
            msg = (
                exc.message_dict.get("phase") if hasattr(exc, "message_dict") else None
            )
            messages.error(request, msg[0] if msg else str(exc))
    else:
        err = form.errors.get("phase")
        messages.error(request, err[0] if err else "相位切换失败")

    if _wants_htmx(request):
        hearth.refresh_from_db()
        resp = render(request, "floor/_drawer.html", _drawer_context(hearth))
        resp["HX-Trigger"] = "floor-refresh"
        return resp
    return redirect(f"/?hearth={pk}")


@login_required
@require_POST
def add_probe(request, pk):
    hearth = get_object_or_404(FireHearth, pk=pk)
    open_run = hearth.open_run()
    if open_run is None:
        messages.error(request, "没有进行中的值守，无法登记探针")
        return redirect(f"/?hearth={pk}")

    form = SoftPointProbeForm(request.POST)
    if form.is_valid():
        probe = form.save(commit=False)
        probe.run = open_run
        probe.save()
        messages.success(request, f"已登记探针 {probe.softPointC}℃")
    else:
        messages.error(request, "探针登记失败，请检查输入")

    if _wants_htmx(request):
        resp = render(request, "floor/_drawer.html", _drawer_context(hearth))
        resp["HX-Trigger"] = "floor-refresh"
        return resp
    return redirect(f"/?hearth={pk}")


@login_required
@require_POST
def open_run(request, pk):
    hearth = get_object_or_404(FireHearth, pk=pk)
    form = OpenCookRunForm(request.POST, hearth=hearth)
    if form.is_valid():
        run = form.save(commit=False)
        run.hearth = hearth
        run.save()
        if hearth.phase == FireHearth.PHASE_COLD:
            hearth.phase = FireHearth.PHASE_CHARGING
            hearth.save(update_fields=["phase"])
        messages.success(request, "新值守已开灶")
    else:
        for errs in form.errors.values():
            for e in errs:
                messages.error(request, e)
            break

    if _wants_htmx(request):
        hearth.refresh_from_db()
        resp = render(request, "floor/_drawer.html", _drawer_context(hearth))
        resp["HX-Trigger"] = "floor-refresh"
        return resp
    return redirect(f"/?hearth={pk}")


@login_required
@require_POST
def close_run(request, pk):
    hearth = get_object_or_404(FireHearth, pk=pk)
    open_run = hearth.open_run()
    if open_run is None:
        messages.error(request, "没有进行中的值守可收灶")
    else:
        open_run.closedAt = timezone.now()
        open_run.save(update_fields=["closedAt"])
        hearth.phase = FireHearth.PHASE_COLD
        hearth.save(update_fields=["phase"])
        messages.success(request, "值守已收灶，灶台回冷灶")

    if _wants_htmx(request):
        hearth.refresh_from_db()
        resp = render(request, "floor/_drawer.html", _drawer_context(hearth))
        resp["HX-Trigger"] = "floor-refresh"
        return resp
    return redirect(f"/?hearth={pk}")


@login_required
@require_http_methods(["GET", "POST"])
def resin_lot_feed(request):
    if request.method == "POST":
        form = ResinLotForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "来脂批已登记")
            return redirect("resin_lot_feed")
    else:
        form = ResinLotForm(
            initial={
                "receivedAt": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            }
        )

    lots = ResinLot.objects.all()[:40]
    return render(request, "resin/feed.html", {"lots": lots, "form": form})


@login_required
@require_http_methods(["GET", "POST"])
def night_duty_cards(request):
    """夜班在岗卡：建卡 / 改上限列表页。"""
    if request.method == "POST":
        form = NightDutyCardForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(
                request,
                f"夜班在岗卡已建：过道 {form.instance.lane} · "
                f"{form.instance.dutyDate:%Y-%m-%d} · {form.instance.shiftName}",
            )
            return redirect("night_duty_cards")
    else:
        form = NightDutyCardForm()

    cards = list(NightDutyCard.objects.all()[:60])
    today = timezone.localdate()
    for card in cards:
        # 仅当日卡显示实时在岗数；历史卡只留档
        card.live_active = (
            on_duty_hearth_count(card.lane) if card.dutyDate == today else None
        )
    return render(
        request,
        "duty/cards.html",
        {"cards": cards, "form": form, "today": today},
    )


@login_required
@require_http_methods(["GET", "POST"])
def edit_night_duty_card(request, pk):
    """改上限（改完下一笔改相位立即吃新上限，计数本就实时查表）。"""
    card = get_object_or_404(NightDutyCard, pk=pk)
    if request.method == "POST":
        form = NightDutyCardForm(request.POST, instance=card)
        if form.is_valid():
            form.save()
            messages.success(
                request,
                f"在岗卡已更新：过道 {card.lane} 上限 → {card.maxActiveHearths}",
            )
            return redirect("night_duty_cards")
    else:
        form = NightDutyCardForm(instance=card)
    return render(request, "duty/card_edit.html", {"card": card, "form": form})
