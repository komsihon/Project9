from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _
from djangotoolbox.fields import ListField

from core.models import AbstractWatchModel
from ikwen.accesscontrol.backends import UMBRELLA

from ikwen.core.models import Model, Service
from ikwen.accesscontrol.models import Member


WELCOME = 'Welcome'
PURCHASE = 'Purchase'
REWARD_PACK_OFFER = 'RewardPackOffer'


class Coupon(AbstractWatchModel):
    """
    A coupon that a Member may collect
    to obtain a concrete Ticket
    """
    UPLOAD_TO = 'coupons'
    DISCOUNT = 'Discount'
    PURCHASE_ORDER = 'PurchaseOrder'
    GIFT = 'Gift'
    TYPE_CHOICES = (
        (DISCOUNT, _("Discount")),
        (PURCHASE_ORDER, _("Purchase Order")),
        (GIFT, _("Gift")),
    )

    PENDING_FOR_APPROVAL = 'PendingForApproval'
    APPROVED = 'Approved'
    STATUS_CHOICES = (
        (PENDING_FOR_APPROVAL, _("Pending for approval")),
        (APPROVED, _("Approved")),
    )

    service = models.ForeignKey(Service)
    name = models.CharField(max_length=60, db_index=True,
                            help_text=_("Name of the coupon"))
    slug = models.SlugField(db_index=True)
    image = models.ImageField(upload_to=UPLOAD_TO, width_field=300, height_field=400,
                              null=getattr(settings, 'DEBUG', False))
    type = models.CharField(max_length=30, choices=TYPE_CHOICES)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES,
                              default=PENDING_FOR_APPROVAL, db_index=True)
    description = models.TextField()
    coefficient = models.IntegerField(default=1, db_index=True)
    heap_size = models.IntegerField(db_index=True, default=100,
                                    help_text="How many of this coupons must "
                                              "collected to obtain a concrete Ticket")
    month_quota = models.IntegerField(help_text=_("How many people must win in the current month."))
    month_winners = models.IntegerField(default=0,
                                        help_text=_("How many winners there have been since the 1st of "
                                                      "the month."))
    is_active = models.BooleanField(default=True)

    offered_history = ListField()
    total_offered = models.IntegerField(default=0)

    class Meta:
        unique_together = ('service', 'name')


class Reward(Model):
    """
    A coupon reward issued by the system either
    for free of after an online payment
    """
    # Statuses
    PREPARED = 'Prepared'
    SENT = 'Sent'

    # Type of reward
    JOIN = 'Join'  # Received after joining community
    FREE = 'Free'  # Received for free
    PAYMENT = 'Payment'  # Received after online payment

    service = models.ForeignKey(Service)
    member = models.ForeignKey(Member)
    coupon = models.ForeignKey(Coupon, db_index=True)
    count = models.IntegerField(default=0)
    status = models.CharField(max_length=15, default=PREPARED, db_index=True)
    type = models.CharField(max_length=15, default=FREE, db_index=True)


class MemberCoupon(Model):
    member = models.ForeignKey(Member)
    coupon = models.ForeignKey(Coupon)

    class Meta:
        abstract = True


class CouponWinner(MemberCoupon):
    """
    Winner of a Coupon
    """


class CumulatedCoupon(MemberCoupon):
    """
    Cumulated Coupon Rewards for a Member
    """
    count = models.IntegerField(default=0)

    class Meta:
        unique_together = ('member', 'coupon', )


class CouponSummary(Model):
    """
    Summary of coupons earned by a Member on a Service
    regardless of what coupon it is.
    """
    service = models.ForeignKey(Service)
    member = models.ForeignKey(Member)
    count = models.IntegerField(default=0)
    threshold_reached = models.BooleanField(default=False)

    class Meta:
        unique_together = ('service', 'member', )


class CouponUse(MemberCoupon):
    """
    Keeps track of how a Coupon was used
    """
    PAYMENT = "Payment"
    DONATION = "Donation"

    count = models.IntegerField(default=0)
    usage = models.CharField(max_length=30,
                             help_text="How the coupon heap was used. Either during a Payment or by donating.")
    object_id = models.CharField(max_length=30)


class CRProfile(Model):
    """
    Member conf information on a community
    """
    FREE_REWARD = 3
    PAYMENT_REWARD = 2
    member = models.OneToOneField(Member)
    reward_score = models.IntegerField(default=1, db_index=True)
    coupon_score = models.IntegerField(db_index=True)
    last_reward_date = models.DateTimeField(db_index=True)
    has_pending_ticket = models.BooleanField(default=False, db_index=True)

    def set_coupon_score(self):
        score = 0
        for cumul in CumulatedCoupon.objects.filter(member=self.member):
            score += cumul.count * cumul.coupon.coefficient
        self.coupon_score = score


class CRBillingPlan(Model):
    """
    Billing Plan ikwen charge Operator who
    want to launch a conf campaign
    """
    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField()
    audience_size = models.IntegerField()
    raw_monthly_cost = models.FloatField()
    sms_monthly_cost = models.FloatField()


class CROperatorProfile(AbstractWatchModel):
    service = models.OneToOneField(Service)
    plan = models.ForeignKey(CRBillingPlan)
    sms = models.BooleanField(default=False)
    auto_renew = models.BooleanField(default=True)
    expiry = models.DateField(db_index=True)

    push_history = ListField()
    # We intentionally write purchaseorder instead of purchase_order: DO NOT CHANGE THAT !
    purchaseorder_history = ListField()
    gift_history = ListField()
    discount_history = ListField()

    total_push = models.IntegerField(default=0)
    # We intentionally write purchaseorder instead of purchase_order: DO NOT CHANGE THAT !
    total_purchaseorder = models.IntegerField(default=0)
    total_gift = models.IntegerField(default=0)
    total_discount = models.IntegerField(default=0)

    def get_status(self):
        count_pending = Coupon.objects.using(UMBRELLA)\
            .filter(service=self.service, status=Coupon.PENDING_FOR_APPROVAL).count()
        if count_pending > 0:
            return Coupon.PENDING_FOR_APPROVAL
        return Coupon.APPROVED


class EarnedReward(Model):
    """
    Reward earned for your action on a Service
    """
    service = models.ForeignKey(Service)
    coupon = models.ForeignKey(Coupon)
    count = models.IntegerField(default=10)

    class Meta:
        abstract = True


class JoinRewardPack(EarnedReward):
    """
    Reward earned for joining a community
    """


class PaymentRewardPack(EarnedReward):
    """
    Reward offered to a Member for an online payment
    of an amount between floor and ceiling
    """
    floor = models.PositiveIntegerField(default=0)
    ceiling = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = (
            ('service', 'coupon', 'floor', ),
            ('service', 'coupon', 'ceiling', ),
        )
