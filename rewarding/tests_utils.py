# -*- coding: utf-8 -*-
import json

from django.core.management import call_command
from django.test.client import Client
from django.test.utils import override_settings
from django.utils import unittest
from django.conf import settings

from ikwen.accesscontrol.models import Member
from ikwen.accesscontrol.backends import UMBRELLA
from ikwen.core.models import Service
from ikwen.rewarding.models import Coupon, Reward, JoinRewardPack, CumulatedCoupon, CouponSummary, PaymentRewardPack, \
    ReferralRewardPack, CouponUse
from ikwen.rewarding.utils import reward_member, use_coupon, donate_coupon
from ikwen.rewarding.tests_views import wipe_test_data


class RewardingUtilsTestCase(unittest.TestCase):
    """
    This test derives django.utils.unittest.TestCate rather than the default django.test.TestCase.
    Thus, self.client is not automatically created and fixtures not automatically loaded. This
    will be achieved manually by a custom implementation of setUp()
    """
    fixtures = ['ikwen_members.yaml', 'setup_data.yaml']

    def setUp(self):
        self.client = Client()
        call_command('loaddata', 'ikwen_members.yaml', database=UMBRELLA)
        call_command('loaddata', 'setup_data.yaml', database=UMBRELLA)
        call_command('loaddata', 'rewarding.yaml', database=UMBRELLA)
        for fixture in self.fixtures:
            call_command('loaddata', fixture)

    def tearDown(self):
        wipe_test_data()
        wipe_test_data(UMBRELLA)

    @override_settings(IKWEN_SERVICE_ID='56eb6d04b37b3379b531b102')
    def test_reward_member_with_member_joining(self):
        member = Member.objects.using(UMBRELLA).get(username='member3')
        service = Service.objects.using(UMBRELLA).get(pk=getattr(settings, 'IKWEN_SERVICE_ID'))
        reward_member(service, member, Reward.JOIN)
        total_count = 0
        for jr in JoinRewardPack.objects.using(UMBRELLA).all():
            coupon = jr.coupon
            total_count += jr.count
            Reward.objects.using(UMBRELLA).get(member=member, coupon=coupon, count=jr.count,
                                               type=Reward.JOIN, status=Reward.SENT)
            cumul = CumulatedCoupon.objects.using(UMBRELLA).get(member=member, coupon=coupon)
            self.assertEqual(cumul.count, jr.count)
        scs = CouponSummary.objects.using(UMBRELLA).get(service=service, member=member)
        self.assertEqual(scs.count, total_count)

    @override_settings(IKWEN_SERVICE_ID='56eb6d04b37b3379b531b102')
    def test_reward_member_with_referral_reward(self):
        member = Member.objects.using(UMBRELLA).get(username='member3')
        service = Service.objects.using(UMBRELLA).get(pk=getattr(settings, 'IKWEN_SERVICE_ID'))
        reward_member(service, member, Reward.REFERRAL)
        total_count = 0
        for ref in ReferralRewardPack.objects.using(UMBRELLA).all():
            coupon = ref.coupon
            total_count += ref.count
            Reward.objects.using(UMBRELLA).get(member=member, coupon=coupon, count=ref.count,
                                               type=Reward.REFERRAL, status=Reward.SENT)
            cumul = CumulatedCoupon.objects.using(UMBRELLA).get(member=member, coupon=coupon)
            self.assertEqual(cumul.count, ref.count)
        scs = CouponSummary.objects.using(UMBRELLA).get(service=service, member=member)
        self.assertEqual(scs.count, total_count)

    @override_settings(IKWEN_SERVICE_ID='56eb6d04b37b3379b531b102')
    def test_reward_member_with_member_payment(self):
        member = Member.objects.using(UMBRELLA).get(username='member3')
        service = Service.objects.using(UMBRELLA).get(pk=getattr(settings, 'IKWEN_SERVICE_ID'))
        amount = 8000
        reward_member(service, member, Reward.PAYMENT, amount=amount,
                      object_id='56eb6d04b37b3379b531b102', model_name='core.Service')
        total_count = 0
        for pr in PaymentRewardPack.objects.using(UMBRELLA).filter(service=service, floor__lt=amount, ceiling__gte=amount):
            coupon = pr.coupon
            total_count += pr.count
            Reward.objects.using(UMBRELLA).get(member=member, coupon=coupon, count=pr.count,
                                               type=Reward.PAYMENT, status=Reward.SENT)
            cumul = CumulatedCoupon.objects.using(UMBRELLA).get(member=member, coupon=coupon)
            self.assertEqual(cumul.count, pr.count)
        scs = CouponSummary.objects.using(UMBRELLA).get(service=service, member=member)
        self.assertEqual(scs.count, total_count)

    @override_settings(IKWEN_SERVICE_ID='56eb6d04b37b3379b531b102')
    def test_use_coupon(self):
        member = Member.objects.using(UMBRELLA).get(username='member3')
        coupon = Coupon.objects.using(UMBRELLA).get(pk='593928184fc0c279dc0f73b1')
        # use_coupon raises a CumulatedCoupon.DoesNotExist error if Member does not have that coupon
        self.assertRaises(CumulatedCoupon.DoesNotExist, use_coupon, member, coupon, 'obj_id')

        CumulatedCoupon.objects.using(UMBRELLA).create(member=member, coupon=coupon, count=65)
        # use_coupon raises a ValueError
        self.assertRaises(ValueError, use_coupon, member, coupon, 'obj_id')

        CumulatedCoupon.objects.using(UMBRELLA).filter(member=member, coupon=coupon).update(count=125)
        use_coupon(member, coupon, 'obj_id')
        CouponUse.objects.using(UMBRELLA).get(member=member, coupon=coupon,
                                              usage=CouponUse.PAYMENT, count=coupon.heap_size)
        cumul = CumulatedCoupon.objects.using(UMBRELLA).get(member=member, coupon=coupon)
        self.assertEqual(cumul.count, 25)

    @override_settings(IKWEN_SERVICE_ID='56eb6d04b37b3379b531b102', UNIT_TESTING=True)
    def test_donate_coupon(self):
        donor = Member.objects.using(UMBRELLA).get(username='member3')
        receiver = Member.objects.using(UMBRELLA).get(username='member4')
        coupon = Coupon.objects.using(UMBRELLA).get(pk='593928184fc0c279dc0f73b1')
        count = 10
        # use_coupon raises a CumulatedCoupon.DoesNotExist error if Member does not have that coupon
        self.assertRaises(CumulatedCoupon.DoesNotExist, donate_coupon, donor, receiver, coupon, 10, 'obj_id')

        CumulatedCoupon.objects.using(UMBRELLA).create(member=donor, coupon=coupon, count=30)
        # use_coupon raises a ValueError
        self.assertRaises(ValueError, donate_coupon, donor, receiver, coupon, 40, 'obj_id')

        donate_coupon(donor, receiver, coupon, count, 'obj_id')
        CouponUse.objects.using(UMBRELLA).get(member=donor, coupon=coupon,
                                              usage=CouponUse.DONATION, count=count)
        donor_cumul = CumulatedCoupon.objects.using(UMBRELLA).get(member=donor, coupon=coupon)
        self.assertEqual(donor_cumul.count, 20)

        receiver_cumul = CumulatedCoupon.objects.using(UMBRELLA).get(member=receiver, coupon=coupon)
        self.assertEqual(receiver_cumul.count, 10)
