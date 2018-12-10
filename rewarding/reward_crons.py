#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import random
import logging

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ikwen.conf.settings")

from datetime import datetime, timedelta
from django.conf import settings
from django.core import mail
from django.core.mail import EmailMessage
from django.utils.translation import gettext as _, activate
from ikwen.accesscontrol.models import Member

from ikwen.core.models import Service
from ikwen.core.utils import get_service_instance, add_event, set_counters, add_database
from ikwen.core.utils import get_mail_content, increment_history_field

from ikwen.rewarding.models import CROperatorProfile, Reward, Coupon, CRProfile, JoinRewardPack, CumulatedCoupon, \
    CouponSummary, CouponWinner, FREE_REWARD_OFFERED
from ikwen.rewarding.utils import get_last_reward

from ikwen.core.log import CRONS_LOGGING
logging.config.dictConfig(CRONS_LOGGING)
logger = logging.getLogger('ikwen.crons')


# CRITICAL_LIMIT = getattr(settings, 'FREE_COUPON_CRITICAL_LIMIT', 75)  # Above this limit we add more free coupons very carefully
# MAX_FREE = getattr(settings, 'CR_MAX_FREE', 42)  # Maximum of free coupons offered
# MIN_FREE = getattr(settings, 'CR_MIN_FREE', 4)  # Minimum of free coupons offered
# MIN_FOR_SENDING = getattr(settings, 'CR_MIN_FOR_SENDING', 0)  # Minimum number of companies involved in a free reward
# MAX_NRM_DAYS = getattr(settings, 'CR_MAX_NRM_DAYS', 3)  # NRM: No Rewarding Mail

now = datetime.now()  # Reference time for the whole script
DEBUG = False


def offer_free_coupon(service, member, profile, coupon, cumul):
    MAX_FREE = getattr(settings, 'CR_MAX_FREE', 30)
    MIN_FREE = getattr(settings, 'CR_MIN_FREE', 4)
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
    next_month = now.month + 1
    if next_month > 12:
        next_month = next_month % 12
    year = now.year if now.month < 12 else now.year + 1
    first_of_next_month = datetime(year, next_month, 1)
    return (first_of_next_month - now).days


def group_rewards_by_service(member):
    """
    Goes through all reward prepared for a member,
    Set those reward actually and groups them by
    Service in a dictionary object which keys are
    the Service and values are matching reward_list
    """
    grouped_rewards = {}
    service_list = []
    for service_id in member.customer_on_fk_list:
        try:
            service_list.append(Service.objects.get(pk=service_id))
        except Service.DoesNotExist:
            pass
    for service in service_list:
        reward_list = []
        try:
            operator = CROperatorProfile.objects.get(service=service, is_active=True)
        except CROperatorProfile.DoesNotExist:
            continue
        set_counters(operator)
        for coupon in Coupon.objects.filter(service=service, status=Coupon.APPROVED, is_active=True):
            try:
                reward = Reward.objects.get(member=member, coupon=coupon, status=Reward.PREPARED)
                reward_list.append(reward)
                cumul, update = CumulatedCoupon.objects.get_or_create(member=member, coupon=coupon)
                cumul.count += reward.count
                cumul.save()
                summary, update = CouponSummary.objects.get_or_create(service=service, member=member)
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


def prepare_free_rewards():
    """
    Prepares rewards to be sent further
    """
    t0 = datetime.now()
    for operator in CROperatorProfile.objects.filter(is_active=True):
        N = operator.plan.audience_size / 30
        service = operator.service
        db = service.database
        add_database(db)
        never_rewarded_count = 0

        # Start the list with member that have never
        # been rewarded. Those with a None last_reward

        # Processing members in a Member.objects.all() may cause
        # segmentation_fault() error. So split into chunks of 500
        total = Member.objects.using(db).all().count()
        chunks = total / 500 + 1
        for i in range(chunks):
            start = i * 500
            finish = (i + 1) * 500
            if DEBUG:
                # Process only superusers in debug mode
                member_queryset = Member.objects.using(db).filter(is_superuser=True)
            else:
                member_queryset = Member.objects.using(db)
            for member in member_queryset[start:finish]:
                profile, update = CRProfile.objects.using(db).get_or_create(member=member)
                last_reward = get_last_reward(member, service)
                if not last_reward and never_rewarded_count < N:
                    never_rewarded_count += 1
                    for coupon in Coupon.objects.filter(service=service, is_active=True, status=Coupon.APPROVED):
                        try:
                            reward_pack = JoinRewardPack.objects.get(service=service, coupon=coupon, count__gt=0)
                            member_u = Member.objects.get(pk=member.id)  # Member from umbrella
                            Reward.objects.create(service=service, member=member_u, coupon=coupon,
                                                  count=reward_pack.count, type=Reward.JOIN, status=Reward.PREPARED)
                            profile.coupon_score += reward_pack.count * coupon.coefficient
                            profile.last_reward_date = datetime.now()
                        except:
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
            coupon_list.append(coupon)
        i = 0
        for profile in CRProfile.objects.using(db).filter(last_reward_date__lte=two_days_back).order_by('reward_score', 'coupon_score', 'last_reward_date')[:n]:
            member = profile.member
            member_u = Member.objects.get(pk=member.id)  # Member from umbrella database
            if DEBUG and not member.is_superuser:
                continue  # Process only superusers in debug mode
            for coupon in coupon_list:
                if i in coupon.winning_indexes:
                    cumul, update = CumulatedCoupon.objects.get_or_create(member=member_u, coupon=coupon)
                    remaining = coupon.heap_size - cumul.count
                    if remaining < 0:
                        remaining = 0
                    extra = random.randrange(3, 20, 3)
                    count = remaining + extra
                    reward, update = Reward.objects.get_or_create(service=service, member=member_u, coupon=coupon,
                                                                  type=Reward.FREE, status=Reward.PREPARED)
                    reward.count += count
                    reward.save()
                    profile.coupon_score += count * coupon.coefficient
                    profile.last_reward_date = datetime.now()
                    coupon.month_winners += 1
                    coupon.save()
                    break
            else:
                shuffled_coupon_list = list(coupon_list)
                random.shuffle(shuffled_coupon_list)
                all_coupon_critical = True
                for coupon in shuffled_coupon_list:
                    cumul, update = CumulatedCoupon.objects.get_or_create(member=member_u, coupon=coupon)
                    CRITICAL_LIMIT = getattr(settings, 'FREE_COUPON_CRITICAL_LIMIT', 75)
                    if cumul.count >= CRITICAL_LIMIT:
                        continue
                    all_coupon_critical = False
                    offer_free_coupon(service, member_u, profile, coupon, cumul)
                    break
                if all_coupon_critical:
                    coupon = random.choice(coupon_list)
                    cumul = CumulatedCoupon.objects.get(member=member_u, coupon=coupon)
                    offer_free_coupon(service, member_u, profile, coupon, cumul)
            profile.reward_score = CRProfile.FREE_REWARD
            profile.save()
            i += 1
    duration = datetime.now() - t0
    logger.debug("prepare_free_rewards() run in %d seconds" % duration.seconds)


def send_free_rewards():
    """
    This cron task regularly sends free rewards
    to ikwen member
    """
    ikwen_service = get_service_instance()
    connection = mail.get_connection()
    try:
        connection.open()
    except:
        logger.error(u"Connexion error", exc_info=True)
    t0 = datetime.now()
    member_list = set()
    reward_sent = 0
    mail_sent = 0
    for reward in Reward.objects.select_related('coupon, member').filter(status=Reward.PREPARED):
        member = reward.member
        if member in member_list:
            continue
        reward_qs = Reward.objects.filter(member=member, status=Reward.PREPARED)
        reward_count = reward_qs.count()
        last_reward = reward_qs.order_by('-id')[0]
        diff = t0 - last_reward.created_on
        MIN_FOR_SENDING = getattr(settings, 'CR_MIN_FOR_SENDING', 1)
        MAX_NRM_DAYS = getattr(settings, 'CR_MAX_NRM_DAYS', 3)
        if reward_count >= MIN_FOR_SENDING or diff.days >= MAX_NRM_DAYS:
            member_list.add(member)
            grouped_rewards = group_rewards_by_service(member)
            reward_sent += 1
            total_coupon = 0
            total_companies = 0
            project_name_list = []
            summary = []
            for service, reward_list in grouped_rewards.items():
                coupons = ['%s: %d' % (reward.coupon.name, reward.count) for reward in reward_list]
                val = service.project_name + ' ' + ','.join(coupons)
                total_coupon += reward.count
                total_companies += 1
                project_name_list.append(service.project_name)
                summary.append(val)
            summary = ' - '.join(summary)
            add_event(ikwen_service, FREE_REWARD_OFFERED, member=member, )
            if member.email:
                if member.language:
                    activate(member.language)
                else:
                    activate('en')
                subject = _("%d free coupons are waiting for you" % total_coupon)
                html_content = get_mail_content(subject, '', template_name='rewarding/mails/free_reward.html',
                                                extra_context={'member_name': member.first_name,
                                                               'grouped_rewards': grouped_rewards,
                                                               'total_coupon': total_coupon, 'total_companies': total_companies,
                                                               'project_names': ','.join(project_name_list)})
                sender = 'ikwen <no-reply@ikwen.com>'
                msg = EmailMessage(subject, html_content, sender, [member.email])
                msg.content_subtype = "html"
                try:
                    if msg.send():
                        mail_sent += 1
                        logger.debug(u"Free reward sent to %s: %s. %s" % (member.username, member.email, summary))
                    else:
                        logger.error(u"Free reward set but not sent to %s: %s. %s" % (member.username, member.email, summary),
                                     exc_info=True)
                except:
                    logger.error(u"Free reward set but not sent to %s: %s. %s" % (member.username, member.email, summary),
                                 exc_info=True)

            # if sms_text:
            #     if member.phone:
            #         if config.sms_sending_method == Config.HTTP_API:
            #             send_sms(member.phone, sms_text)
            #         else:
            #             QueuedSMS.objects.create(recipient=member.phone, text=sms_text)
    for member in member_list:
        Reward.objects.filter(member=member, status=Reward.PREPARED).update(status=Reward.SENT)
    try:
        connection.close()
    finally:
        pass
    duration = datetime.now() - t0
    logger.debug("send_free_rewards() run in %d seconds" % duration.seconds)


# def remind_100_coupon_reached():
#     """
#     Cron job that revive users for pending 100 coupons
#     """
#     t0 = datetime.now()
#     total_revival, total_mail = 0, 0
#     total = CouponSummary.objects.filter(threshold_reached=True).count()
#     chunks = total / 500
#     for i in range(chunks):
#         start = i * 500
#         finish = (i + 1) * 500
#         for summary in CouponSummary.objects.filter(threshold_reached=True)[start:finish]:
#
#     for operator in CROperatorProfile.objects.filter(is_active=True):
#         N = operator.plan.audience_size / 30
#         service = operator.service
#         db = service.database
#         add_database(db)
#
#         # Processing members in a Member.objects.all() may cause
#         # segmentation_fault() error. So split into chunks of 500
#         total = Member.objects.using(db).all().count()
#         chunks = total / 500 + 1
#         for i in range(chunks):
#             start = i * 500
#             finish = (i + 1) * 500
#             if DEBUG:
#                 # Process only superusers in debug mode
#                 member_queryset = Member.objects.using(db).filter(is_superuser=True)
#             else:
#                 member_queryset = Member.objects.using(db)
#             for member in member_queryset[start:finish]:
#                 profile, update = CRProfile.objects.using(db).get_or_create(member=member)
#                 last_reward = get_last_reward(member, service)
#
#         balance = Balance.objects.using(WALLETS_DB_ALIAS).get(service_id=service.id)
#         if balance.mail_count == 0:
#             notify_for_empty_messaging_credit(service, EMAIL)
#             continue
#         tk = revival.model_name.split('.')
#         model = get_model(tk[0], tk[1])
#         try:
#             obj = model._default_manager.using(db).get(pk=revival.object_id)
#             object_profile = ObjectProfile.objects.using(db).get(object_id=revival.object_id)
#         except ObjectDoesNotExist:
#             continue
#         profile_tag_list = []
#         for tag in object_profile.tag_list:
#             try:
#                 profile_tag = ProfileTag.objects.using(db).get(slug=tag)
#                 profile_tag_list.append(profile_tag)
#             except:
#                 continue
#
#         if revival.status != PENDING:
#             continue
#
#         set_counters_many(*profile_tag_list)
#         revival_local = Revival.objects.using(db).get(pk=revival.id)
#         if debug:
#             member_queryset = Member.objects.using(db).filter(is_superuser=True)
#         else:
#             member_queryset = Member.objects.using(db).all()
#         total = member_queryset.count()
#         chunks = total / 500 + 1
#         for i in range(chunks):
#             start = i * 500
#             finish = (i + 1) * 500
#             for member in member_queryset.order_by('date_joined')[start:finish]:
#                 try:
#                     profile = MemberProfile.objects.using(db).get(member=member)
#                 except MemberProfile.DoesNotExist:
#                     profile = MemberProfile.objects.using(db).create(member=member, tag_list=[REFERRAL])
#                 match = set(profile.tag_list) & set(object_profile.tag_list)
#                 if len(match) > 0:
#                     if debug:
#                         print "Profiles matching on %s for member %s" % (match, member)
#                     if member.email:
#                         Target.objects.using(db).get_or_create(revival=revival_local, member=member)
#         revival.run_on = datetime.now()
#         revival.status = STARTED
#         revival.total = revival_local.target_set.all().count()
#         revival.save()
#
#         connection = mail.get_connection()
#         try:
#             connection.open()
#         except:
#             logger.error(u"Connexion error", exc_info=True)
#
#         kwargs = {}
#         if revival.get_kwargs:
#             get_kwargs = import_by_path(revival.get_kwargs)
#             kwargs = get_kwargs(revival)
#
#         for target in revival_local.target_set.select_related('member').filter(notified=False)[:MAX_BATCH_SEND]:
#             member = target.member
#             if member.language:
#                 activate(member.language)
#             else:
#                 activate('en')
#             mail_renderer = import_by_path(revival.mail_renderer)
#             subject, html_content = mail_renderer(target, obj, revival, **kwargs)
#             if not html_content:
#                 continue
#             if debug:
#                 subject = 'Test - ' + subject
#             sender = '%s <no-reply@%s>' % (service.project_name, service.domain)
#             msg = EmailMessage(subject, html_content, sender, [member.email])
#             msg.content_subtype = "html"
#             try:
#                 with transaction.atomic(using=WALLETS_DB_ALIAS):
#                     if not debug:
#                         balance.mail_count -= 1
#                         balance.save()
#                     if msg.send():
#                         target.revival_count += 1
#                         target.notified = True
#                         target.save()
#                         total_mail += 1
#                         increment_history_field_many('smart_revival_history', args=profile_tag_list)
#                     else:
#                         logger.error("Member %s not notified for Content %s" % (member.email, str(obj)),
#                                      exc_info=True)
#             except:
#                 logger.error("Member %s not notified for Content %s" % (member.email, str(obj)), exc_info=True)
#             revival.progress += 1
#             revival.save()
#         else:
#             revival.is_running = False
#             if revival.progress > 0 and revival.progress >= revival.total:
#                 revival.status = COMPLETE
#             revival.save()
#
#         try:
#             connection.close()
#         except:
#             pass
#
#     diff = datetime.now() - t0
#     logger.debug("notify_profiles() run %d revivals. %d mails sent in %s" % (total_revival, total_mail, diff))


if __name__ == "__main__":
    try:
        try:
            DEBUG = sys.argv[1] == 'debug'
        except IndexError:
            DEBUG = False
        yesterday = now - timedelta(days=1)
        if yesterday.month != now.month:
            Coupon.objects.update(month_winners=0)
        prepare_free_rewards()
        send_free_rewards()
    except:
        logger.error(u"Fatal error occured", exc_info=True)
