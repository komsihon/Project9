# -*- coding: utf-8 -*-
import json
from datetime import timedelta

from django.core.management import call_command
from django.test.client import Client
from django.test.utils import override_settings
from django.utils import unittest

from ikwen.core.utils import get_service_instance, add_database
from ikwen.accesscontrol.backends import ARCH_EMAIL
from ikwen.rewarding.tests_views import wipe_test_data
from ikwen.rewarding.models import *
from ikwen.rewarding.utils import *

from ikwen.rewarding.reward_crons import prepare_free_rewards, send_free_rewards


class RewardingRewardingCronsTestCase(unittest.TestCase):
    """
    This test derives django.utils.unittest.TestCate rather than the default django.test.TestCase.
    Thus, self.client is not automatically created and fixtures not automatically loaded. This
    will be achieved manually by a custom implementation of setUp()
    """
    fixtures = ['ikwen_members.yaml', 'setup_data.yaml']

    def setUp(self):
        self.client = Client()
        call_command('loaddata', 'rewarding.yaml', database=UMBRELLA)
        for fixture in self.fixtures:
            call_command('loaddata', fixture)
        db = 'test_ikwen_service_2'
        add_database(db)
        for fixture in self.fixtures:
            call_command('loaddata', fixture, database=db)

    def tearDown(self):
        wipe_test_data()
        wipe_test_data(UMBRELLA)
        wipe_test_data('test_ikwen_service_2')

    @override_settings(IKWEN_SERVICE_ID='56eb6d04b37b3379b531b102',
                       EMAIL_BACKEND='django.core.mail.backends.filebased.EmailBackend',
                       EMAIL_FILE_PATH='test_emails/rewarding/')
    def test_send_free_rewards_with_members_never_rewarded(self):
        service = Service.objects.using(UMBRELLA).get(pk=getattr(settings, 'IKWEN_SERVICE_ID'))
        db = service.database
        add_database(db)
        for member in Member.objects.all():
            member.customer_on_fk_list = ['56eb6d04b37b3379b531b101', '56eb6d04b37b3379b531b102', '56eb6d04b37b3379b531b103']
            member.save()
        prepare_free_rewards()
        send_free_rewards()
        for member in Member.objects.using(db).all():
            cr_profile = CRProfile.objects.using(db).get(member=member)
            self.assertEqual(cr_profile.reward_score, CRProfile.FREE_REWARD)
            total_count = 0
            for coupon in Coupon.objects.filter(service=service, status=Coupon.APPROVED, is_active=True):
                try:
                    reward_pack = JoinRewardPack.objects.get(service=service, coupon=coupon, count__gt=0)
                    cumul = CumulatedCoupon.objects.get(member=member, coupon=coupon)
                    Reward.objects.get(service=service, member=member, coupon=coupon,
                                       count=reward_pack.count, status=Reward.SENT, type=Reward.JOIN)
                    self.assertEqual(cumul.count, reward_pack.count)
                    total_count += reward_pack.count
                except JoinRewardPack.DoesNotExist:
                    pass
            summary = CouponSummary.objects.get(service=service, member=member)
            self.assertEqual(summary.count, total_count)

    @override_settings(IKWEN_SERVICE_ID='56eb6d04b37b3379b531b102',
                       EMAIL_BACKEND='django.core.mail.backends.filebased.EmailBackend',
                       EMAIL_FILE_PATH='test_emails/rewarding/',
                       CR_MIN_FOR_SENDING=0)
    def test_send_free_rewards_with_members_previously_rewarded(self):
        service = Service.objects.get(pk=getattr(settings, 'IKWEN_SERVICE_ID'))
        db = service.database
        add_database(db)
        last_reward_date = datetime.now() - timedelta(days=10)
        c1 = Coupon.objects.get(pk='593928184fc0c279dc0f73b1')
        for member in Member.objects.exclude(email=ARCH_EMAIL):
            member_local = Member.objects.using(db).get(pk=member.id)
            CRProfile.objects.using(db).create(member=member_local, last_reward_date=last_reward_date)
            CumulatedCoupon.objects.create(member=member, coupon=c1, count=30)
            Reward.objects.create(service=service, member=member, coupon=c1, count=30, type=Reward.JOIN, status=Reward.SENT)
            CouponSummary.objects.create(service=service, member=member, count=30)
            member.customer_on_fk_list = ['56eb6d04b37b3379b531b101', '56eb6d04b37b3379b531b102', '56eb6d04b37b3379b531b103']
            member.save()
        prepare_free_rewards()
        send_free_rewards()
        for member in Member.objects.using(db).exclude(email=ARCH_EMAIL):
            cr_profile = CRProfile.objects.using(db).get(member=member)
            self.assertEqual(cr_profile.reward_score, CRProfile.FREE_REWARD)
            reward = Reward.objects.get(service=service, member=member, status=Reward.SENT, type=Reward.FREE)
            coupon = reward.coupon
            cumul = CumulatedCoupon.objects.get(member=member, coupon=coupon)
            if coupon == c1:
                self.assertGreater(cumul.count, 30)
            else:
                self.assertGreater(cumul.count, 0)
            summary = CouponSummary.objects.get(service=service, member=member)
            self.assertGreater(summary.count, 30)
