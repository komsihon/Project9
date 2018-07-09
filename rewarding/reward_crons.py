#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import random

from core.utils import increment_history_field
from rewarding.models import CouponWinner

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ikwen.conf.settings")

from django.db.models import Q
from datetime import datetime, timedelta
from django.conf import settings
from django.contrib.auth.models import Group
from django.core import mail
from django.core.mail import EmailMessage
from django.core.urlresolvers import reverse
from django.utils import timezone
from django.utils.module_loading import import_by_path
from django.utils.log import AdminEmailHandler
from django.utils.translation import gettext as _
from ikwen.accesscontrol.models import Member, SUDO

from ikwen.core.models import Config, QueuedSMS, Service
from ikwen.core.utils import get_service_instance, send_sms, add_event, set_counters
from ikwen.core.utils import get_mail_content
from ikwen.billing.models import Invoice, InvoicingConfig, INVOICES_SENT_EVENT, \
    NEW_INVOICE_EVENT, INVOICE_REMINDER_EVENT, REMINDERS_SENT_EVENT, OVERDUE_NOTICE_EVENT, OVERDUE_NOTICES_SENT_EVENT, \
    SUSPENSION_NOTICES_SENT_EVENT, SERVICE_SUSPENDED_EVENT, SendingReport
from ikwen.billing.utils import get_invoice_generated_message, get_invoice_reminder_message, \
    get_invoice_overdue_message, \
    get_service_suspension_message, get_next_invoice_number, get_subscription_model, get_billing_cycle_months_count, \
    pay_with_wallet_balance

from ikwen.accesscontrol.backends import UMBRELLA
from ikwen.rewarding.models import CROperatorProfile, Reward, Coupon, CRProfile, JoinRewardPack, CumulatedCoupon, \
    CouponSummary
from ikwen.rewarding.utils import get_last_reward, get_coupon_score

import logging.handlers
logger = logging.getLogger('ikwen.rewarding')


CRITICAL_LIMIT = 75
MAX_FREE = 42
MIN_FREE = 4
MIN_FOR_SENDING = 2
MAX_NRM_DAYS = 3  # NRM: No Rewarding Mail


def prepare_rewards():
    """

    """
    t0 = datetime.now()
    for operator in CROperatorProfile.objects.filter(expiry__gt=t0):
        N = operator.plan.audience_size / 30
        service = operator.service
        db = service.database
        never_rewarded_count = []

        # Start the list with member that have never
        # been rewarded. Those with a None last_reward
        for member in Member.objects.using(db).all():
            profile, update = CRProfile.objects.using(db).get_or_create(member=member.id)
            last_reward = get_last_reward(member, service)
            if not last_reward and never_rewarded_count < N:
                never_rewarded_count += 1
                for coupon in Coupon.objects.filter(service=service):
                    try:
                        reward_pack = JoinRewardPack.objects.get(service=service, coupon=coupon)
                        if reward_pack.count > 0:
                            mbr = Member.objects.get(pk=member.id)  # Member from umbrella
                            Reward.objects.create(service=service, member=mbr, coupon=coupon,
                                                  count=reward_pack.count, type=Reward.JOIN, status=Reward.PREPARED)
                            profile.coupon_score += reward_pack.count * coupon.coefficient
                            profile.last_reward_date = datetime.now()
                    except JoinRewardPack.DoesNotExist:
                        continue
            profile.reward_score = CRProfile.FREE_REWARD
            profile.save()

        # Add extra members to reach N people
        n = N - never_rewarded_count
        two_days_back = t0 - timedelta(days=2)
        remaining_days = get_remaining_days_in_month()
        list_n = list(range(n))  # Generate a list of
        coupon_list = []
        for coupon in Coupon.objects.filter(service=service, status=Coupon.APPROVED, is_active=True):
            winners_count = coupon.month_quota - coupon.month_winners
            winners_today = winners_count / remaining_days
            coupon.winning_indexes = random.sample(list_n, winners_today)
        shuffled_coupon_list = list(coupon_list)
        i = 0
        for profile in CRProfile.objects.using(db).filter(last_reward_date__lte=two_days_back).order_by('reward_score', 'coupon_score', 'last_reward_date')[:n]:
            member = profile.member
            mbr = Member.objects.get(pk=member.id)  # Member from umbrella database
            for coupon in coupon_list:
                if i in coupon.winning_indexes:
                    cumul, update = CumulatedCoupon.objects.get_or_create(member=mbr, coupon=coupon)
                    remaining = coupon.heap_size - cumul.count
                    if remaining < 0:
                        remaining = 0
                    extra = random.randrange(3, 20, 3)
                    count = remaining + extra
                    reward, update = Reward.objects.get_or_create(service=service, member=mbr, coupon=coupon,
                                                                  type=Reward.FREE, status=Reward.PREPARED)
                    reward.count += count
                    reward.save()
                    profile.coupon_score += count * coupon.coefficient
                    profile.last_reward_date = datetime.now()
                    coupon.month_winners += 1
                    coupon.save()
                    break
            else:
                random.shuffle(shuffled_coupon_list)
                all_coupon_critical = True
                for coupon in shuffled_coupon_list:
                    cumul, update = CumulatedCoupon.objects.get_or_create(member=mbr, coupon=coupon)
                    if cumul.count >= CRITICAL_LIMIT:
                        continue
                    all_coupon_critical = False
                    offer_free_coupon(service, mbr, profile, coupon, cumul)
                if all_coupon_critical:
                    coupon = random.choice(coupon_list)
                    cumul = CumulatedCoupon.objects.get(member=mbr, coupon=coupon)
                    offer_free_coupon(service, mbr, profile, coupon, cumul)
            profile.reward_score = CRProfile.FREE_REWARD
            profile.save()
            i += 1
    duration = datetime.now() - t0

def offer_free_coupon(service, member, profile, coupon, cumul):
    left = coupon.heap_size - cumul.count - 5
    max_count = min(MAX_FREE, left) / coupon.coefficient
    min_count = min(MIN_FREE, max_count)
    count = random.randint(min_count, max_count)
    reward, update = Reward.objects.get_or_create(service=service, member=member, coupon=coupon,
                                                  type=Reward.FREE, status=Reward.PREPARED)
    reward.count += count
    reward.save()
    profile.coupon_score += count * coupon.coefficient
    profile.last_reward_date = datetime.now()


def get_remaining_days_in_month():
    now = datetime.now()
    next_month = (now.month + 1) % 12
    year = now.year if now.month < 12 else now.year + 1
    first_of_next_month = datetime(year, next_month, 1)
    return (first_of_next_month - now).days


def group_rewards_by_service(member):
    grouped_rewards = {}
    for service in member.customer_on:
        reward_list = []
        operator = CROperatorProfile.objects.get(service=service)
        set_counters(operator)
        for coupon in Coupon.objects.filter(service=service, status=Coupon.APPROVED, is_active=True):
            try:
                reward = Reward.objects.get(member=member, coupon=coupon, status=Reward.PREPARED)
                reward_list.append(reward)
                cumul, update = CumulatedCoupon.objects.get_or_create(member=member, coupon=coupon)
                cumul.count += reward.count
                cumul.save()
                summary, update = CouponSummary.objects.using(UMBRELLA).get_or_create(service=service, member=member)
                summary.count += reward.count
                if cumul.count > coupon.heap_size:
                    CouponWinner.objects.create(member=member, coupon=coupon)
                    summary.threshold_reached = True
                summary.save()
                history_field = coupon.type.lower() + '_history'
                increment_history_field(operator, history_field, reward.count)

                set_counters(coupon)
                increment_history_field(coupon, 'offered_history', reward.count)
            except Reward.DoesNotExist:
                pass

        increment_history_field(operator, 'push_history')
        grouped_rewards[service] = reward_list
    return grouped_rewards


def send_free_rewards():
    """
    This cron task regularly sends free rewards
    to ikwen member
    """
    connection = mail.get_connection()
    try:
        connection.open()
    except:
        logger.error(u"Connexion error", exc_info=True)
    t0 = datetime.now()
    member_list = set()
    for reward in Reward.objects.filter(status=Reward.PREPARED):
        member = reward.member
        if member in member_list:
            continue
        reward_qs = Reward.objects.filter(member=member, status=Reward.PREPARED)
        reward_count = reward_qs.count()
        last_reward = reward_qs.order_by('-id')[0]
        diff = t0 - last_reward.created_on
        if reward_count >= MIN_FOR_SENDING or diff.days >= MAX_NRM_DAYS:
            member_list.add(member)
            grouped_rewards = group_rewards_by_service(member)
            summary = []
            for service, reward_list in grouped_rewards:
                coupons = ['%s: %d' % (reward.coupon.name, reward.count) for reward in reward_list]
                val = service.project_name + ' ' + ','.join(coupons)
                summary.append(val)
            summary = ' - '.join(summary)
            if member.email:
                subject = _("Free coupons are waiting for you")
                html_content = get_mail_content(subject, '', template_name='rewarding/mails/free_reward.html',
                                                extra_context={'grouped_rewards': grouped_rewards})
                sender = 'ikwen <no-reply@ikwen.com>'
                msg = EmailMessage(subject, html_content, sender, [member.email])
                msg.content_subtype = "html"
                try:
                    if msg.send():
                        logger.debug(u"Free reward sent to %s: %s. %s" % (member.username, member.email, summary))
                    else:
                        logger.error(u"Free reward set but not sent to %s: %s. %s" % (member.username, member.email, summary),
                                     exc_info=True)
                except:
                    logger.error(u"Free reward set but not sent to %s: %s. %s" % (member.username, member.email, summary),
                                 exc_info=True)

            if sms_text:
                if member.phone:
                    if config.sms_sending_method == Config.HTTP_API:
                        send_sms(member.phone, sms_text)
                    else:
                        QueuedSMS.objects.create(recipient=member.phone, text=sms_text)
    for member in member_list:
        Reward.objects.filter(member=member, status=Reward.PREPARED).update(status=Reward.SENT)
    try:
        connection.close()
    finally:
        if count > 0:
            report = SendingReport.objects.create(count=count, total_amount=total_amount)
            sudo_group = Group.objects.get(name=SUDO)
            add_event(service, INVOICES_SENT_EVENT, group_id=sudo_group.id, object_id=report.id)
    duration = datetime.now() - t0


if __name__ == "__main__":
    try:
        prepare_rewards()
        send_free_rewards()
    except:
        logger.error(u"Fatal error occured", exc_info=True)
