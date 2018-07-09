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
    for name in ('Coupon', 'CRBillingPlan', 'Reward', 'CumulatedCoupon', 'CouponSummary',
                 'CouponUse', 'CROperatorProfile', 'JoinRewardPack', 'PaymentRewardPack', ):
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
    def test_Configuration_save_billing(self):
        self.client.login(username='member2', password='admin')
        response = self.client.get(reverse('rewarding:configuration'),
                                   {'action': 'save_billing', 'plan_id': '593984fc0c279dc0f73b2281', 'sms': 'on'})
        self.assertEqual(response.status_code, 200)
        json_response = json.loads(response.content)
        self.assertTrue(json_response['success'])

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
                    "rewards": [{"coupon_id": id1, "count": 10}, {"coupon_id": id2, "count": 15}]
                },
                {
                    "floor": 5000, "ceiling": 15000,
                    "rewards": [{"coupon_id": id1, "count": 20}, {"coupon_id": id2, "count": 30}]
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
        PaymentRewardPack.objects.using(UMBRELLA).get(coupon=c1, count=20, floor=5000, ceiling=15000)
        PaymentRewardPack.objects.using(UMBRELLA).get(coupon=c2, count=30, floor=5000, ceiling=15000)

    @override_settings(IKWEN_SERVICE_ID='56eb6d04b37b3379b531b102')
    def test_Dashboard(self):
        """
        Make sure the url is reachable
        """
        self.client.login(username='member2', password='admin')
        response = self.client.get(reverse('rewarding:dashboard'))
        self.assertEqual(response.status_code, 200)
