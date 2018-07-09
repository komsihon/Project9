from datetime import datetime
from django.utils.translation import gettext as _
from ikwen.accesscontrol.backends import UMBRELLA
from ikwen.rewarding.models import Coupon, JoinRewardPack, CumulatedCoupon, PaymentRewardPack, Reward, CouponSummary, \
    CouponUse, CRProfile, CouponWinner


def reward_member(service, member, type, **kwargs):
    reward_pack = None
    profile, update = CRProfile.objects.get_or_create(member=member.id)
    coupon_summary, update = CouponSummary.objects.using(UMBRELLA).get_or_create(service=service, member=member)
    if type == Reward.JOIN:
        for coupon in Coupon.objects.using(UMBRELLA).filter(service=service):
            try:
                reward_pack = JoinRewardPack.objects.using(UMBRELLA).get(service=service, coupon=coupon)
                if reward_pack.count > 0:
                    cumul, update = CumulatedCoupon.objects.using(UMBRELLA).get_or_create(member=member, coupon=coupon)
                    cumul.count += reward_pack.count
                    cumul.save()
                    Reward.objects.using(UMBRELLA).create(service=service, member=member, coupon=coupon,
                                                          count=reward_pack.count, type=Reward.JOIN, status=Reward.SENT)
                    coupon_summary.count += reward_pack.count
                    if cumul.count >= coupon.heap_size:
                        CouponWinner.objects.using(UMBRELLA).create(member=member, coupon=coupon)
                        coupon_summary.threshold_reached = True
                    profile.coupon_score += reward_pack.count * coupon.coefficient
            except JoinRewardPack.DoesNotExist:
                continue
        else:
            profile.reward_score = CRProfile.FREE_REWARD
    elif type == Reward.PAYMENT:
        amount = kwargs.pop('amount')
        for coupon in Coupon.objects.using(UMBRELLA).filter(service=service):
            try:
                reward_pack = PaymentRewardPack.objects.using(UMBRELLA).get(service=service, coupon=coupon,
                                                                            floor__lt=amount, ceiling__gte=amount)
                if reward_pack.count > 0:
                    cumul, update = CumulatedCoupon.objects.using(UMBRELLA).get_or_create(member=member, coupon=coupon)
                    cumul.count += reward_pack.count
                    cumul.save()
                    Reward.objects.using(UMBRELLA).create(service=service, member=member, coupon=coupon,
                                                          count=reward_pack.count, type=Reward.PAYMENT, status=Reward.SENT)
                    coupon_summary.count += reward_pack.count
                    if cumul.count >= coupon.heap_size:
                        CouponWinner.objects.using(UMBRELLA).create(member=member, coupon=coupon)
                        coupon_summary.threshold_reached = True
                    coupon_summary.save()
                    profile.coupon_score += reward_pack.count * coupon.coefficient
            except PaymentRewardPack.DoesNotExist:
                continue
        else:
            profile.reward_score = CRProfile.PAYMENT_REWARD
    profile.last_reward_date = datetime.now()
    profile.save()
    coupon_summary.save()
    return reward_pack


def get_last_reward(member, service):
    try:
        return Reward.objects.filter(member=member, service=service).order_by('-id')[0]
    except IndexError:
        return None


def get_coupon_score(member):
    score = 0
    for cumul in CumulatedCoupon.objects.filter(member=member):
        score += cumul.count * cumul.coupon.coefficient
    return score


def send_payment_reward_email(member):
    """
    Sends a mail that informs the member what he actually
    earned for his online payment.
    @param member: Member object to whom message is sent
    """
    service = get_service_instance()
    config = service.config
    subject = _("Welcome to %s" % service.project_name)
    if config.welcome_message:
        message = config.welcome_message.replace('$member_name', member.first_name)
    else:
        message = _("Welcome %(member_name)s,<br><br>"
                    "Your registration was successful and you can now enjoy our service.<br><br>"
                    "Thank you." % {'member_name': member.first_name})
    html_content = get_mail_content(subject, message, template_name='accesscontrol/mails/welcome.html')
    sender = '%s <no-reply@%s>' % (service.project_name, service.domain)
    msg = EmailMessage(subject, html_content, sender, [member.email])
    msg.content_subtype = "html"
    Thread(target=lambda m: m.send(), args=(msg,)).start()


def use_coupon(member, coupon, object_id):
    cumul = CumulatedCoupon.objects.using(UMBRELLA).get(member=member, coupon=coupon)
    if cumul.count < coupon.heap_size:
        raise ValueError("Insufficient coupons to be consumed. "
                         "Need %d of them, found only %d" % (coupon.heap_size, cumul.count))
    cumul.count -= coupon.heap_size
    cumul.save()
    CouponUse.objects.using(UMBRELLA).create(member=member, coupon=coupon,
                                             usage=CouponUse.PAYMENT, object_id=object_id, count=coupon.heap_size)


def donate_coupon(member, coupon, count, object_id):
    cumul = CumulatedCoupon.objects.using(UMBRELLA).get(member=member, coupon=coupon)
    if cumul.count < count:
        raise ValueError("Insufficient coupons to be donated. found only %d" % cumul.count)
    cumul.count -= count
    cumul.save()
    CouponUse.objects.using(UMBRELLA).create(member=member, coupon=coupon,
                                             usage=CouponUse.DONATION, object_id=object_id, count=count)
