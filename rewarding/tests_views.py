# -*- coding: utf-8 -*-
import json

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.urlresolvers import reverse
from django.test.client import Client
from django.test.utils import override_settings
from django.utils import unittest
from ikwen.rewarding.models import *


def wipe_test_data(alias='default'):
    """
    This test was originally built with django-nonrel 1.6 which had an error when flushing the database after
    each test. So the flush is performed manually with this custom tearDown()
    """
    import ikwen.core.models
    import ikwen.accesscontrol.models
    import ikwen.rewarding.models
    import permission_backend_nonrel.models

    Group.objects.using(alias).all().delete()
    for name in ('Application', 'Service', 'Config', 'ConsoleEventType', 'ConsoleEvent', 'Country', ):
        model = getattr(ikwen.core.models, name)
        model.objects.using(alias).all().delete()
    for name in ('Member', 'AccessRequest', ):
        model = getattr(ikwen.accesscontrol.models, name)
        model.objects.using(alias).all().delete()
    for name in ('Coupon', 'CRBillingPlan', 'Reward', 'CumulatedCoupon', 'CouponSummary', 'CouponUse',
                 'CouponWinner', 'CRProfile', 'CROperatorProfile', 'JoinRewardPack', 'PaymentRewardPack', ):
        model = getattr(ikwen.rewarding.models, name)
        model.objects.using(alias).all().delete()
    for name in ('UserPermissionList', 'GroupPermissionList',):
        model = getattr(permission_backend_nonrel.models, name)
        model.objects.using(alias).all().delete()


class RewardingViewsTestCase(unittest.TestCase):
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
    def test_Configuration(self):
        """
        Make sure the url is reachable
        """
        self.client.login(username='member2', password='admin')
        response = self.client.get(reverse('rewarding:configuration'))
        self.assertEqual(response.status_code, 200)

    @override_settings(IKWEN_SERVICE_ID='56eb6d04b37b3379b531b102')
    def test_Configuration_activate(self):
        self.client.login(username='member2', password='admin')
        response = self.client.get(reverse('rewarding:configuration'), {'action': 'activate'})
        self.assertEqual(response.status_code, 302)
        service = get_service_instance()
        cr_profile = CROperatorProfile.objects.using(UMBRELLA).get(service=service)

    @override_settings(IKWEN_SERVICE_ID='56eb6d04b37b3379b531b102', UNIT_TESTING=True)
    def test_Configuration_delete_coupon(self):
        call_command('loaddata', 'collected.yaml', database=UMBRELLA)
        s = '56eb6d04b37b3379b531b102'
        m1 = '56eb6d04b37b3379b531e011'
        m2 = '56eb6d04b37b3379b531e012'
        c = '593928184fc0c279dc0f73b1'
        self.client.login(username='member2', password='admin')
        response = self.client.get(reverse('rewarding:configuration'), {'action': 'delete', 'selection': c})
        self.assertEqual(response.status_code, 200)
        summary1 = CouponSummary.objects.get(service=s, member=m1)
        summary2 = CouponSummary.objects.get(service=s, member=m2)
        self.assertRaises(CumulatedCoupon.DoesNotExist, CumulatedCoupon.objects.get, member=m1, coupon=c)
        self.assertRaises(CumulatedCoupon.DoesNotExist, CumulatedCoupon.objects.get, member=m2, coupon=c)
        self.assertRaises(CouponWinner.DoesNotExist, CouponWinner.objects.get, member=m2, coupon=c)
        self.assertIsNotNone(CouponWinner.objects.get(member=m1, coupon=c))
        self.assertEqual(summary1.count, 110)
        self.assertTrue(summary1.threshold_reached)
        self.assertEqual(summary2.count, 50)
        self.assertFalse(summary2.threshold_reached)

    @override_settings(IKWEN_SERVICE_ID='56eb6d04b37b3379b531b102')
    def test_Configuration_save_billing(self):
        self.client.login(username='member2', password='admin')
        service = get_service_instance()
        plan_id = '593984fc0c279dc0f73b2281'
        plan = CRBillingPlan.objects.get(pk=plan_id)
        response = self.client.post(reverse('rewarding:configuration') + '?action=save_billing',
                                    {'plan_id': plan_id, 'sms_push': 'on'})
        cr_profile = CROperatorProfile.objects.using(UMBRELLA).get(service=service)
        self.assertEqual(cr_profile.plan, plan)

    @override_settings(IKWEN_SERVICE_ID='56eb6d04b37b3379b531b102')
    def test_Configuration_save_rewards_packs(self):
        """
        Make sure that configuration is correctly saved
        and that all previous configuration is wiped out
        """
        id1 = '593928184fc0c279dc0f73b1'
        id2 = '593928184fc0c279dc0f73b2'
        self.client.login(username='member2', password='admin')
        data = {
            "join": [{"coupon_id": id1, "count": 10}, {"coupon_id": id2, "count": 10}],
            "payment": [
                {
                    "floor": 0, "ceiling": 5000,
                    "reward_list": [{"coupon_id": id1, "count": 10}, {"coupon_id": id2, "count": 15}]
                },
                {
                    "floor": 5001, "ceiling": 15000,
                    "reward_list": [{"coupon_id": id1, "count": 20}, {"coupon_id": id2, "count": 30}]
                },
            ]
        }
        response = self.client.post(reverse('rewarding:configuration') + '?action=save_rewards_packs', json.dumps(data), 'application/json')
        json_response = json.loads(response.content)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(json_response['success'])
        c1 = Coupon.objects.using(UMBRELLA).get(pk=id1)
        c2 = Coupon.objects.using(UMBRELLA).get(pk=id2)

        # Verify that the following were correctly created
        self.assertEqual(JoinRewardPack.objects.using(UMBRELLA).all().count(), 2)
        JoinRewardPack.objects.using(UMBRELLA).get(coupon=c1, count=10)
        JoinRewardPack.objects.using(UMBRELLA).get(coupon=c2, count=10)

        self.assertEqual(PaymentRewardPack.objects.using(UMBRELLA).all().count(), 4)
        PaymentRewardPack.objects.using(UMBRELLA).get(coupon=c1, count=10, floor=0, ceiling=5000)
        PaymentRewardPack.objects.using(UMBRELLA).get(coupon=c2, count=15, floor=0, ceiling=5000)
        PaymentRewardPack.objects.using(UMBRELLA).get(coupon=c1, count=20, floor=5001, ceiling=15000)
        PaymentRewardPack.objects.using(UMBRELLA).get(coupon=c2, count=30, floor=5001, ceiling=15000)

    @override_settings(IKWEN_SERVICE_ID='56eb6d04b37b3379b531b102')
    def test_Dashboard(self):
        """
        Make sure the url is reachable
        """
        self.client.login(username='member2', password='admin')
        response = self.client.get(reverse('rewarding:dashboard'))
        self.assertEqual(response.status_code, 200)

    @override_settings(IKWEN_SERVICE_ID='56eb6d04b37b3379b531b102')
    def test_CouponDetail(self):
        """
        Make sure the url is reachable
        """
        response = self.client.get(reverse('rewarding:coupon_detail'), {'id': '593928184fc0c279dc0f73b1'})
        self.assertEqual(response.status_code, 200)
        json_response = json.loads(response.content)
