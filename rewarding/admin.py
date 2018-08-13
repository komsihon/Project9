from threading import Thread

from django.conf import settings
from django.contrib import admin, messages
from django.core.mail import EmailMessage
from django.utils.translation import gettext as _

from ikwen.core.utils import get_mail_content
from ikwen.rewarding.models import Coupon, CRBillingPlan, CROperatorProfile


class CouponAdmin(admin.ModelAdmin):
    list_display = ('service', 'name', 'type', 'month_quota', 'month_winners', 'status', 'is_active', 'deleted', )
    list_filter = ('status', 'type', )
    actions = ['approve_coupons', 'reject_coupons']
    if getattr(settings, 'IS_IKWEN', False):
        readonly_fields = ('service', 'type', 'month_quota', 'month_winners', 'total_offered')
    else:
        fields = ('name', 'type', 'description', 'month_quota', )

    def approve_coupons(self, request, queryset):
        email = queryset[0].service.member.email
        template_name = 'rewarding/mails/coupon_approved.html'
        subject = _("Your coupons were approved")
        html_content = get_mail_content(subject, template_name=template_name,
                                        extra_context={'coupon_list': queryset})
        sender = 'ikwen <no-reply@ikwen.com>'
        msg = EmailMessage(subject, html_content, sender, [email])
        msg.content_subtype = "html"
        msg.bcc = ['contact@ikwen.com']
        Thread(target=lambda m: m.send(), args=(msg,)).start()

    def reject_coupons(self, request, queryset):
        email = queryset[0].service.member.email
        template_name = 'rewarding/mails/coupon_rejected.html'
        subject = _("Your coupons were rejected")
        html_content = get_mail_content(subject, template_name=template_name,
                                        extra_context={'coupon_list': queryset})
        sender = 'ikwen <no-reply@ikwen.com>'
        msg = EmailMessage(subject, html_content, sender, [email])
        msg.content_subtype = "html"
        msg.bcc = ['contact@ikwen.com']
        Thread(target=lambda m: m.send(), args=(msg,)).start()


class CRBillingPlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'audience_size', 'raw_monthly_cost', 'sms_monthly_cost', 'is_active', )
    search_fields = ('name', )
    prepopulated_fields = {"slug": ("name",)}


class CROperatorProfileAdmin(admin.ModelAdmin):
    list_display = ('service', 'plan', 'billing_cycle', 'monthly_cost', 'expiry', 'is_active', )
    search_fields = ('name', )

    def save_model(self, request, obj, form, change):
        # Only superuser can extend expiry
        if not change:
            messages.error(request, "CR Operator Profile can only be created through activation on user's platform")
            return
        before = CROperatorProfile.objects.get(pk=obj.id)
        if before.expiry != obj.expiry or before.monthly_cost != obj.monthly_cost or before.is_active != obj.is_active:
            if not request.user.is_superuser:
                messages.error(request, "Only superuser may modify expiry, monthly cost or state")
                return
        super(CROperatorProfileAdmin, self).save_model(request, obj, form, change)


if getattr(settings, 'IS_IKWEN', False):
    admin.site.register(Coupon, CouponAdmin)
    admin.site.register(CRBillingPlan, CRBillingPlanAdmin)
    admin.site.register(CROperatorProfile, CROperatorProfileAdmin)
