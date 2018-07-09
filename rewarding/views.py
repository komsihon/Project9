import json

from django.conf import settings
from django.http.response import HttpResponse
from django.shortcuts import render
from django.views.generic import TemplateView, DetailView

from ikwen.core.models import Service
from ikwen.core.utils import get_service_instance
from ikwen.accesscontrol.backends import UMBRELLA
from ikwen.core.views import ChangeObjectBase
from ikwen.rewarding.models import Coupon, JoinRewardPack, PaymentRewardPack, CRBillingPlan, CROperatorProfile
from ikwen.rewarding.admin import CouponAdmin

CONTINUOUS_REWARDING = 'Continuous Rewarding'


class Dashboard(TemplateView):
    template_name = 'rewarding/dashboard.html'


class Configuration(TemplateView):
    template_name = 'rewarding/configuration.html'

    def get(self, request, *args, **kwargs):
        action = request.GET.get('action')
        if action == 'save_billing':
            return self.save_billing(request)
        return super(Configuration, self).get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        action = request.GET.get('action')
        if action == 'save_rewards_packs':
            return self.save_rewards_packs(request)
        return super(Configuration, self).get(request, *args, **kwargs)

    def save_billing(self, request):
        plan_id = request.GET['plan_id']
        sms = request.GET.get('sms')
        auto_renew = request.GET.get('auto_renew')
        plan = CRBillingPlan.objects.using(UMBRELLA).get(pk=plan_id)
        service = get_service_instance()
        cr_profile = CROperatorProfile.objects.using(UMBRELLA).get(service=service)
        cr_profile.plan = plan
        cr_profile.sms = True if sms else False
        cr_profile.auto_renew = True if auto_renew else False
        cr_profile.save()
        return HttpResponse(json.dumps({'success': True}))

    def save_rewards_packs(self, request):
        rewards = json.loads(request.body)
        service_id = getattr(settings, 'IKWEN_SERVICE_ID')
        service = Service.objects.using(UMBRELLA).get(pk=service_id)
        # Delete all previous set JoinRewards ...
        JoinRewardPack.objects.using(UMBRELLA).filter(service=service).delete()

        # ... And set new ones
        for reward in rewards['join']:
            coupon_id = reward['coupon_id']
            coupon = Coupon.objects.using(UMBRELLA).get(pk=coupon_id)
            count = int(reward['count'])
            JoinRewardPack.objects.using(UMBRELLA).create(service=service, coupon=coupon, count=count)

        # Delete all previous set PurchaseRewards ...
        PaymentRewardPack.objects.using(UMBRELLA).filter(service=service).delete()

        # ... And set new ones
        for interval in rewards['payment']:
            floor = int(interval['floor'])
            ceiling = int(interval['ceiling'])
            for reward in interval['rewards']:
                coupon_id = reward['coupon_id']
                coupon = Coupon.objects.using(UMBRELLA).get(pk=coupon_id)
                count = int(reward['count'])
                PaymentRewardPack.objects.using(UMBRELLA).create(service=service, coupon=coupon,
                                                                 floor=floor, ceiling=ceiling, count=count)
        return HttpResponse(json.dumps({'success': True}))


class ChangeCoupon(ChangeObjectBase):
    model = Coupon
    model_admin = CouponAdmin
    object_list_url = 'rewarding:configuration'
    template_name = 'rewarding/change_coupon.html'

    def get_context_data(self, **kwargs):
        context = super(ChangeCoupon, self).get_context_data(**kwargs)
        context['verbose_name_plural'] = CONTINUOUS_REWARDING
        return context


class CouponDetail(DetailView):
    model = Coupon