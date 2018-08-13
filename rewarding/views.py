import json

from datetime import datetime, timedelta

import os

from ajaxuploader.backends.local import LocalUploadBackend
from ajaxuploader.views import AjaxFileUploader, csrf_exempt
from django.conf import settings
from django.contrib import messages
from django.core.files import File
from django.core.urlresolvers import reverse
from django.http.response import HttpResponse, HttpResponseRedirect, Http404
from django.shortcuts import render, get_object_or_404
from django.template import Context
from django.template.defaultfilters import slugify
from django.template.loader import get_template
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView, DetailView
from django.utils.translation import gettext as _

from ikwen.conf import settings as ikwen_settings
from ikwen.core.models import Service
from ikwen.core.utils import get_service_instance, get_model_admin_instance
from ikwen.accesscontrol.backends import UMBRELLA
from ikwen.core.views import ChangeObjectBase
from ikwen.rewarding.models import Coupon, JoinRewardPack, PaymentRewardPack, CRBillingPlan, CROperatorProfile, \
    CouponWinner
from ikwen.rewarding.admin import CouponAdmin

CONTINUOUS_REWARDING = 'Continuous Rewarding'


class Dashboard(TemplateView):
    template_name = 'rewarding/dashboard.html'


class Configuration(TemplateView):
    template_name = 'rewarding/configuration.html'

    def get_context_data(self, **kwargs):
        context = super(Configuration, self).get_context_data(**kwargs)
        service = get_service_instance()
        dc_coupon_list = Coupon.objects.using(UMBRELLA).filter(service=service, type=Coupon.DISCOUNT)
        po_coupon_list = Coupon.objects.using(UMBRELLA).filter(service=service, type=Coupon.PURCHASE_ORDER)
        gift_coupon_list = Coupon.objects.using(UMBRELLA).filter(service=service, type=Coupon.GIFT)
        context['plan_list'] = CRBillingPlan.objects.using(UMBRELLA).filter(is_active=True)
        payment_interval_list = []
        bound_list = set()
        for reward in PaymentRewardPack.objects.using(UMBRELLA).filter(service=service):
            bound_list.add((reward.floor, reward.ceiling))
        for item in bound_list:
            floor, ceiling = item[0], item[1]
            interval = {'floor': floor, 'ceiling': ceiling}

            coupon_list = []
            for coupon in dc_coupon_list:
                coupon.payment_reward = coupon.get_payment_reward_pack(floor, ceiling)
                coupon_list.append(coupon)
            interval['dc_coupon_list'] = coupon_list

            coupon_list = []
            for coupon in po_coupon_list:
                coupon.payment_reward = coupon.get_payment_reward_pack(floor, ceiling)
                coupon_list.append(coupon)
            interval['po_coupon_list'] = coupon_list

            coupon_list = []
            for coupon in gift_coupon_list:
                coupon.payment_reward = coupon.get_payment_reward_pack(floor, ceiling)
                coupon_list.append(coupon)
            interval['gift_coupon_list'] = coupon_list

            payment_interval_list.append(interval)

        context['dc_coupon_list'] = dc_coupon_list
        context['po_coupon_list'] = po_coupon_list
        context['gift_coupon_list'] = gift_coupon_list
        context['payment_interval_list'] = payment_interval_list
        try:
            context['cr_profile'] = CROperatorProfile.objects.using(UMBRELLA).get(service=service)
        except CROperatorProfile.DoesNotExist:
            pass
        return context

    def get(self, request, *args, **kwargs):
        action = request.GET.get('action')
        if action == 'activate':
            return self.activate()
        elif action == 'save_billing':
            return self.save_billing(request)
        elif action == 'delete':
            return self.delete_coupon(request)
        return super(Configuration, self).get(request, *args, **kwargs)

    @method_decorator(csrf_exempt)
    def dispatch(self, request, *args, **kwargs):
        return super(Configuration, self).dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        action = request.GET.get('action')
        if action == 'save_billing':
            return self.save_billing(request)
        elif action == 'save_rewards_packs':
            return self.save_rewards_packs(request)
        context = self.get_context_data(**kwargs)
        return render(request, self.template_name, context)

    def activate(self):
        service_id = getattr(settings, 'IKWEN_SERVICE_ID')
        service = Service.objects.using(UMBRELLA).get(pk=service_id)
        plan = CRBillingPlan.objects.using(UMBRELLA).get(slug=CRBillingPlan.FREE_TEST)
        expiry = datetime.now() + timedelta(days=90)
        CROperatorProfile.objects.using(UMBRELLA).get_or_create(service=service, plan=plan, expiry=expiry)
        notice = _("Your Continuous Rewarding program is now active.")
        messages.success(self.request, notice)
        next_url = reverse('rewarding:configuration')
        return HttpResponseRedirect(next_url)

    def save_billing(self, request):
        plan_id = request.POST['plan_id']
        sms = request.POST.get('sms_push')
        auto_renew = request.POST.get('auto_renew')
        plan = CRBillingPlan.objects.using(UMBRELLA).get(pk=plan_id)
        service = get_service_instance()
        cr_profile = CROperatorProfile.objects.using(UMBRELLA).get(service=service)
        if cr_profile.plan != plan:
            cr_profile.plan = plan
            cr_profile.sms = True if sms else False
            cr_profile.auto_renew = True if auto_renew else False
            cr_profile.save()
            notice = _("Your Billing Plan was changed to %s." % plan.name)
            messages.success(self.request, notice)
        next_url = reverse('rewarding:configuration')
        return HttpResponseRedirect(next_url)

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

        # Check to make sure intervals do not overlap
        intervals = rewards['payment']
        _cmp = lambda x, y: 1 if x['floor'] > y['floor'] else -1
        intervals.sort(_cmp)
        overlap = False
        for i in range(len(intervals) - 1):
            if int(intervals[i]['ceiling']) >= int(intervals[i+1]['floor']):
                overlap = True
                break
        if overlap:
            i00, i01 = intervals[i]['floor'], intervals[i]['ceiling']
            i10, i11 = intervals[i+1]['floor'], intervals[i+1]['ceiling']
            response = {'error': 'Intervals %s-%s and %s-%s overlap' % (i00, i01, i10, i11)}
            return HttpResponse(json.dumps(response))

        # ... And set new ones
        for interval in intervals:
            floor = int(interval['floor'])
            ceiling = int(interval['ceiling'])
            for reward in interval['reward_list']:
                coupon_id = reward['coupon_id']
                coupon = Coupon.objects.using(UMBRELLA).get(pk=coupon_id)
                count = int(reward['count'])
                PaymentRewardPack.objects.using(UMBRELLA).create(service=service, coupon=coupon,
                                                                 floor=floor, ceiling=ceiling, count=count)
        return HttpResponse(json.dumps({'success': True}))

    def delete_coupon(self, request):
        object_id = request.GET['selection']
        coupon = Coupon.objects.using(UMBRELLA).get(pk=object_id)
        response = {
            'message': "Coupon %s deleted." % coupon.name,
            'deleted': [coupon.id]
        }
        ikwen_media_root = ikwen_settings.MEDIA_ROOT
        if coupon.image.name:
            try:
                os.unlink(ikwen_media_root + coupon.image.name)
            except:
                pass
        coupon.delete()
        return HttpResponse(
            json.dumps(response),
            content_type='application/json'
        )


class ChangeCoupon(ChangeObjectBase):
    model = Coupon
    model_admin = CouponAdmin
    object_list_url = 'rewarding:configuration'
    template_name = 'rewarding/change_coupon.html'

    def get_object(self, **kwargs):
        object_id = kwargs.get('object_id')
        if object_id:
            try:
                return Coupon.objects.using(UMBRELLA).get(pk=object_id)
            except Coupon.DoesNotExist:
                raise Http404('No Coupon matches the given ID.')

    def get_context_data(self, **kwargs):
        context = super(ChangeCoupon, self).get_context_data(**kwargs)
        context['verbose_name_plural'] = CONTINUOUS_REWARDING
        winner_list = set([obj.member for obj in CouponWinner.objects.using(UMBRELLA).filter(collected=False)])
        context['winner_list'] = winner_list
        return context

    def get(self, request, *args, **kwargs):
        action = request.GET.get('action')
        if action == 'delete_image':
            object_id = kwargs.get('object_id')
            obj = get_object_or_404(self.model, pk=object_id)
            image_field_name = request.GET.get('image_field_name', 'image')
            image_field = obj.__getattribute__(image_field_name)
            if image_field.name:
                image_path = ikwen_settings.MEDIA_ROOT + image_field.name
                os.unlink(image_path)
                try:
                    os.unlink(image_field.small_path)
                    os.unlink(image_field.thumb_path)
                except:
                    pass
                obj.__setattr__(image_field_name, None)
                obj.save()
            return HttpResponse(
                json.dumps({'success': True}),
                content_type='application/json'
            )
        return super(ChangeCoupon, self).get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        object_admin = get_model_admin_instance(self.model, self.model_admin)
        object_id = kwargs.get('object_id')
        if object_id:
            obj = self.get_object(**kwargs)
        else:
            obj = self.model()
        model_form = object_admin.get_form(request)
        form = model_form(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            slug_field_name = request.POST.get('slug_field_name', 'slug')
            try:
                obj.__getattribute__(slug_field_name)
                name_field_name = request.POST.get('name_field_name', 'name')
                try:
                    name_field = obj.__getattribute__(name_field_name)
                    obj.__setattr__(slug_field_name, slugify(name_field))
                    obj.save()
                except:
                    pass
            except:
                pass
            image_url = request.POST.get('image_url')
            if image_url:
                s = get_service_instance()
                media_root = getattr(settings, 'MEDIA_ROOT')
                path = image_url.replace(getattr(settings, 'MEDIA_URL'), '')
                path = path.replace(ikwen_settings.MEDIA_URL, '')
                if not obj.image.name or path != obj.image.name:
                    filename = image_url.split('/')[-1]
                    try:
                        with open(media_root + path, 'r') as f:
                            content = File(f)
                            destination = media_root + obj.UPLOAD_TO + "/" + s.project_name_slug + '_' + filename
                            obj.image.save(destination, content)
                            destination2 = destination.replace(media_root, ikwen_settings.MEDIA_ROOT)
                            os.rename(destination, destination2)
                        os.unlink(media_root + path)
                    except IOError as e:
                        if getattr(settings, 'DEBUG', False):
                            raise e
                        return {'error': 'File failed to upload. May be invalid or corrupted image file'}
            self.after_save(request, obj, *args, **kwargs)
            next_url = self.get_object_list_url(request, obj, *args, **kwargs)
            if object_id:
                messages.success(request, obj._meta.verbose_name.capitalize() + ' <strong>' + str(obj).decode('utf8') + '</strong> ' + _('successfully updated'))
            else:
                messages.success(request, obj._meta.verbose_name.capitalize() + ' <strong>' + str(obj).decode('utf8') + '</strong> ' + _('successfully created'))
            return HttpResponseRedirect(next_url)
        else:
            context = self.get_context_data(**kwargs)
            context['errors'] = form.errors
            return render(request, self.template_name, context)


class CouponDetail(DetailView):
    model = Coupon

    def get(self, request, *args, **kwargs):
        coupon_id = request.GET['id']
        try:
            coupon = Coupon.objects.using(UMBRELLA).get(pk=coupon_id)
        except:
            raise Http404("No Coupon found with ID: %s" % coupon_id)
        return HttpResponse(json.dumps(coupon.to_dict()), content_type='application/json')


class CouponUploadBackend(LocalUploadBackend):
    def update_filename(self, request, filename, *args, **kwargs):
        tokens = filename.split('.')
        ext = tokens[-1]
        name = ''.join(tokens[:-1])
        filename = slugify(name) + '.' + ext
        return super(CouponUploadBackend, self).update_filename(request, filename, *args, **kwargs)

    def upload_complete(self, request, filename, *args, **kwargs):
        path = self.UPLOAD_DIR + "/" + filename
        self._dest.close()
        media_root = getattr(settings, 'MEDIA_ROOT')
        media_url = ikwen_settings.MEDIA_URL
        object_id = request.GET.get('object_id')
        if object_id:
            s = get_service_instance()
            coupon = Coupon.objects.using(UMBRELLA).get(pk=object_id)
            try:
                with open(media_root + path, 'r') as f:
                    content = File(f)
                    current_image_path = ikwen_settings.MEDIA_ROOT + coupon.image.name if coupon.image.name else None
                    dir = media_root + coupon.UPLOAD_TO
                    unique_filename = False
                    filename_suffix = 0
                    filename_no_extension, extension = os.path.splitext(filename)
                    seo_filename_no_extension = slugify(coupon.name)
                    seo_filename = s.project_name_slug + '_' + seo_filename_no_extension + extension
                    if os.path.isfile(os.path.join(dir, seo_filename)):
                        while not unique_filename:
                            try:
                                if filename_suffix == 0:
                                    open(os.path.join(dir, seo_filename))
                                else:
                                    open(os.path.join(dir, seo_filename_no_extension + str(filename_suffix) + extension))
                                filename_suffix += 1
                            except IOError:
                                unique_filename = True
                    if filename_suffix > 0:
                        seo_filename = seo_filename_no_extension + str(filename_suffix) + extension

                    destination = os.path.join(dir, seo_filename)
                    if not os.path.exists(dir):
                        os.makedirs(dir)
                    coupon.image.save(destination, content)
                    destination2 = destination.replace(media_root, ikwen_settings.MEDIA_ROOT)
                    os.rename(destination, destination2)
                    url = media_url + coupon.image.name
                try:
                    if coupon.image and os.path.exists(media_root + path):
                        os.unlink(media_root + path)  # Remove file from upload tmp folder
                except Exception as e:
                    if getattr(settings, 'DEBUG', False):
                        raise e
                if current_image_path:
                    try:
                        if destination2 != current_image_path and os.path.exists(current_image_path):
                            os.unlink(current_image_path)
                    except OSError as e:
                        if getattr(settings, 'DEBUG', False):
                            raise e
                return {
                    'path': url
                }
            except IOError as e:
                if settings.DEBUG:
                    raise e
                return {'error': 'File failed to upload. May be invalid or corrupted image file'}
        else:
            return super(CouponUploadBackend, self).upload_complete(request, filename, *args, **kwargs)


upload_coupon_image = AjaxFileUploader(CouponUploadBackend)


def render_reward_offer_event(event, request):
    try:
        access_request = AccessRequest.objects.using(UMBRELLA).get(pk=event.object_id)
    except AccessRequest.DoesNotExist:
        return None
    html_template = get_template('rewarding/events/reward_offer.html')
    database = event.service.database
    add_database_to_settings(database)
    member = Member.objects.using(database).get(pk=event.member.id)
    from ikwen.conf.settings import MEDIA_URL
    c = Context({'IKWEN_MEDIA_URL': MEDIA_URL})
    return html_template.render(c)