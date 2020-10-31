from datetime import datetime

from django.conf import settings
from ikwen.core.models import Service
from ikwen.core.utils import add_event
from ikwen.accesscontrol.backends import UMBRELLA
from ikwen.accesscontrol.models import Member
from ikwen.rewarding.models import Coupon, JoinRewardPack, CumulatedCoupon, PaymentRewardPack, Reward, CouponSummary, \
    CouponUse, CRProfile, CouponWinner, WELCOME_REWARD_OFFERED, PAYMENT_REWARD_OFFERED, CROperatorProfile, \
    REFERRAL_REWARD_OFFERED, MANUAL_REWARD_OFFERED, ReferralRewardPack

JOIN = '__Join'
REFERRAL = '__Referral'


def reward_member(service, member, type, **kwargs):
    """
    Rewards a Member on a Service according the the type
    of reward.

    :param service: Service on which the member is interacting
    :param member: Member actually concerned
    :param type: Type of reward. Can be Reward.JOIN, Reward.PAYMENT or Reward.MANUAL
    :param kwargs: kwargs must be provided depending on type.

        # Reward.PAYMENT, following kwargs are expected:
            *amount*: amount actually paid by Member
            *object_id*: ID of the object paid
            *model_name*: Django style model name. *Eg: trade.Order*

        # Reward.MANUAL, following kwargs are expected:
            *coupon*: Coupon being donated to the Member
            *count*: number of coupon given

    :return: A tuple (list of JoinRewardPack or PaymentRewardPack, total_coupon_count)
    """
    now = datetime.now()
    try:
        # All rewarding actions are run only if
        # Operator has an active profile.
        CROperatorProfile.objects.using(UMBRELLA).get(service=service, is_active=True)
    except CROperatorProfile.DoesNotExist:
        return None, 0
    reward_pack = None
    reward_pack_list = []
    coupon_count = 0
    service = Service.objects.using(UMBRELLA).get(pk=service.id)
    member = Member.objects.using(UMBRELLA).get(pk=member.id)
    profile, update = CRProfile.objects.get_or_create(member=member)
    coupon_summary, update = CouponSummary.objects.using(UMBRELLA).get_or_create(service=service, member=member)
    if type == Reward.JOIN:
        for coupon in Coupon.objects.using(UMBRELLA).filter(service=service):
            try:
                reward_pack = JoinRewardPack.objects.using(UMBRELLA).select_related('service', 'coupon')\
                    .get(service=service, coupon=coupon)
                if reward_pack.count > 0:
                    reward_pack_list.append(reward_pack)
                    cumul, update = CumulatedCoupon.objects.using(UMBRELLA).get_or_create(member=member, coupon=coupon)
                    cumul.count += reward_pack.count
                    cumul.save()
                    Reward.objects.using(UMBRELLA).create(service=service, member=member, coupon=coupon,
                                                          count=reward_pack.count, type=Reward.JOIN, status=Reward.SENT)
                    coupon_summary.count += reward_pack.count
                    coupon_count += reward_pack.count
                    if cumul.count >= coupon.heap_size:
                        CouponWinner.objects.using(UMBRELLA).create(member=member, coupon=coupon)
                        coupon_summary.threshold_reached = True
                    profile.coupon_score += reward_pack.count * coupon.coefficient
            except JoinRewardPack.DoesNotExist:
                continue
        else:
            profile.reward_score = CRProfile.FREE_REWARD
        if reward_pack:
            add_event(service, WELCOME_REWARD_OFFERED, member)
    elif type == Reward.REFERRAL:
        for coupon in Coupon.objects.using(UMBRELLA).filter(service=service):
            try:
                reward_pack = ReferralRewardPack.objects.using(UMBRELLA).select_related('service', 'coupon')\
                    .get(service=service, coupon=coupon)
                if reward_pack.count > 0:
                    reward_pack_list.append(reward_pack)
                    cumul, update = CumulatedCoupon.objects.using(UMBRELLA).get_or_create(member=member, coupon=coupon)
                    cumul.count += reward_pack.count
                    cumul.save()
                    Reward.objects.using(UMBRELLA).create(service=service, member=member, coupon=coupon,
                                                          count=reward_pack.count, type=Reward.REFERRAL, status=Reward.SENT)
                    coupon_summary.count += reward_pack.count
                    if cumul.count >= coupon.heap_size:
                        CouponWinner.objects.using(UMBRELLA).create(member=member, coupon=coupon)
                        coupon_summary.threshold_reached = True
                    profile.coupon_score += reward_pack.count * coupon.coefficient
                    coupon_count += reward_pack.count
            except ReferralRewardPack.DoesNotExist:
                continue
        else:
            profile.reward_score = CRProfile.FREE_REWARD
        if reward_pack:
            add_event(service, REFERRAL_REWARD_OFFERED, member)
    elif type == Reward.PAYMENT:
        amount = kwargs.pop('amount')
        object_id = kwargs.pop('object_id', None)
        model_name = kwargs.pop('model_name', None)
        for coupon in Coupon.objects.using(UMBRELLA).filter(service=service):
            try:
                reward_pack = PaymentRewardPack.objects.using(UMBRELLA).select_related('service', 'coupon')\
                    .get(service=service, coupon=coupon, floor__lt=amount, ceiling__gte=amount)
                if reward_pack.count > 0:
                    reward_pack_list.append(reward_pack)
                    cumul, update = CumulatedCoupon.objects.using(UMBRELLA).get_or_create(member=member, coupon=coupon)
                    cumul.count += reward_pack.count
                    cumul.save()
                    Reward.objects.using(UMBRELLA).create(service=service, member=member, coupon=coupon,
                                                          count=reward_pack.count, type=Reward.PAYMENT,
                                                          status=Reward.SENT, object_id=object_id, amount=amount)
                    coupon_summary.count += reward_pack.count
                    if cumul.count >= coupon.heap_size:
                        CouponWinner.objects.using(UMBRELLA).create(member=member, coupon=coupon)
                        coupon_summary.threshold_reached = True
                    coupon_summary.save()
                    profile.coupon_score += reward_pack.count * coupon.coefficient
                    coupon_count += reward_pack.count
            except PaymentRewardPack.DoesNotExist:
                continue
        else:
            profile.reward_score = CRProfile.PAYMENT_REWARD
        if reward_pack:
            add_event(service, PAYMENT_REWARD_OFFERED, member, object_id=object_id, model=model_name)
    elif type == Reward.MANUAL:
        coupon = kwargs.pop('coupon')
        count = kwargs.pop('count')
        cumul, update = CumulatedCoupon.objects.using(UMBRELLA).get_or_create(member=member, coupon=coupon)
        cumul.count += count
        cumul.save()
        Reward.objects.using(UMBRELLA).create(service=service, member=member, coupon=coupon,
                                              count=count, type=Reward.MANUAL, status=Reward.SENT)
        coupon_summary.count += count
        if cumul.count >= coupon.heap_size:
            CouponWinner.objects.using(UMBRELLA).create(member=member, coupon=coupon)
            coupon_summary.threshold_reached = True
        coupon_summary.save()
        profile.coupon_score += count * coupon.coefficient
        profile.reward_score = CRProfile.MANUAL_REWARD
        add_event(service, MANUAL_REWARD_OFFERED, member)
    profile.save()
    coupon_summary.save()
    return reward_pack_list, coupon_count


def get_last_reward(member, service):
    try:
        return Reward.objects.filter(member=member, service=service).order_by('-id')[0]
    except IndexError:
        return None


def use_coupon(member, coupon, object_id=None):
    """
    Marks a Coupon heap as used to acquire any item with ID object_id
    :param member: Member that owns coupon
    :param coupon: Coupon being saved
    :param object_id: ID of the item
    """
    service = coupon.service
    cumul = CumulatedCoupon.objects.using(UMBRELLA).get(member=member, coupon=coupon)
    if cumul.count < coupon.heap_size:
        raise ValueError("Insufficient coupons to be consumed. "
                         "Need %d of them, found only %d" % (coupon.heap_size, cumul.count))
    cumul.count -= coupon.heap_size
    cumul.save()
    CouponUse.objects.using(UMBRELLA).create(member=member, coupon=coupon,
                                             usage=CouponUse.PAYMENT, object_id=object_id, count=coupon.heap_size)
    if cumul.count >= coupon.heap_size:
        try:
            coupon_winner = CouponWinner.objects.using(UMBRELLA).filter(member=member, coupon=coupon)[0]
            coupon_winner.collected = True
        except:
            pass
    threshold_reached = False
    for cumul in CumulatedCoupon.objects.using(UMBRELLA).filter(member=member):
        if cumul.count >= cumul.coupon.heap_size:
            threshold_reached = True
            break
    CouponSummary.objects.using(UMBRELLA).filter(service=service, member=member).update(threshold_reached=threshold_reached)


def donate_coupon(donor, receiver, coupon, count, object_id):
    service = coupon.service
    if getattr(settings, 'UNIT_TESTING', False):
        db = 'default'
    else:
        db = service.database
    try:
        Member.objects.using(db).get(pk=receiver.id)
    except Member.DoesNotExist:
        raise ValueError("Donor and Receiver must belong to the same community")
    donor_cumul = CumulatedCoupon.objects.using(UMBRELLA).get(member=donor, coupon=coupon)
    if donor_cumul.count < count:
        raise ValueError("Insufficient coupons to be donated. found only %d" % donor_cumul.count)
    donor_cumul.count -= count
    donor_cumul.save()
    CouponUse.objects.using(UMBRELLA).create(member=donor, coupon=coupon,
                                             usage=CouponUse.DONATION, object_id=object_id, count=count)

    threshold_reached = False
    for donor_cumul in CumulatedCoupon.objects.using(UMBRELLA).filter(member=donor):
        if donor_cumul.count >= donor_cumul.coupon.heap_size:
            threshold_reached = True
            break
    donor_summary, update = CouponSummary.objects.using(UMBRELLA).get_or_create(service=service, member=donor)
    donor_summary.count -= count
    donor_summary.threshold_reached = threshold_reached
    donor_summary.save()

    CRProfile.objects.using(db).get_or_create(member=receiver)
    receiver_cumul, update = CumulatedCoupon.objects.using(UMBRELLA).get_or_create(member=receiver, coupon=coupon)
    receiver_cumul.count += count
    receiver_cumul.save()

    receiver_summary, update = CouponSummary.objects.using(UMBRELLA).get_or_create(service=service, member=receiver)
    receiver_summary.count += count
    if receiver_cumul.count >= coupon.heap_size:
        receiver_summary.threshold_reached = True
    receiver_summary.save()


def get_coupon_summary_list(member):
    member_services = member.get_services()
    active_operators = CROperatorProfile.objects.filter(service__in=member_services, is_active=True)
    active_cr_services = [op.service for op in active_operators]
    coupon_summary_list = member.couponsummary_set.filter(service__in=active_cr_services)
    return coupon_summary_list


def get_join_reward_pack_list(revival=None, service=None):
    if revival:
        service = revival.service
    reward_pack_list = []
    for coupon in Coupon.objects.using(UMBRELLA).filter(service=service, is_active=True, status=Coupon.APPROVED):
        try:
            reward_pack = JoinRewardPack.objects.using(UMBRELLA).select_related('service', 'coupon')\
                .get(service=service, coupon=coupon)
            if reward_pack.count > 0:
                reward_pack_list.append(reward_pack)
        except JoinRewardPack.DoesNotExist:
            continue
    return {'reward_pack_list': reward_pack_list}


def get_referral_reward_pack_list(revival):
    service = revival.service
    reward_pack_list = []
    for coupon in Coupon.objects.using(UMBRELLA).filter(service=service, is_active=True, status=Coupon.APPROVED):
        try:
            reward_pack = ReferralRewardPack.objects.using(UMBRELLA).select_related('service', 'coupon')\
                .get(service=service, coupon=coupon)
            if reward_pack.count > 0:
                reward_pack_list.append(reward_pack)
        except ReferralRewardPack.DoesNotExist:
            continue
    return {'reward_pack_list': reward_pack_list}
