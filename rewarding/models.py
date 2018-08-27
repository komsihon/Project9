from threading import Thread

from django.conf import settings
from django.db import models
from django.db.models.signals import post_save
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from djangotoolbox.fields import ListField

from ikwen.accesscontrol.backends import UMBRELLA

from ikwen.core.models import AbstractWatchModel, Model, Service
from ikwen.core.utils import get_service_instance, to_dict
from ikwen.accesscontrol.models import Member


WELCOME = 'Welcome'
PURCHASE = 'Purchase'
REWARD_PACK_OFFER = 'RewardPackOffer'
FREE_REWARD_OFFERED = 'FreeRewardOffered'
WELCOME_REWARD_OFFERED = 'WelcomeRewardOffered'
PAYMENT_REWARD_OFFERED = 'PaymentRewardOffered'
MANUAL_REWARD_OFFERED = 'ManualRewardOffered'


class Coupon(AbstractWatchModel):
    """
    A coupon that a Member may collect
    to obtain a concrete Ticket
    """
    UPLOAD_TO = 'rewarding/coupons'
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
    REJECTED = 'Rejected'
    STATUS_CHOICES = (
        (PENDING_FOR_APPROVAL, _("Pending for approval")),
        (APPROVED, _("Approved")),
        (REJECTED, _("Rejected")),
    )

    service = models.ForeignKey(Service, default=get_service_instance,
                                related_name='+')
    name = models.CharField(max_length=60, db_index=True,
                            help_text=_("Name of the coupon"))
    slug = models.SlugField(db_index=True)
    image = models.ImageField(upload_to=UPLOAD_TO, blank=True, null=True)
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
    deleted = models.BooleanField(default=False)

    offered_history = ListField(editable=False)
    total_offered = models.IntegerField(default=0)

    class Meta:
        unique_together = (
            ('service', 'name'),
            ('service', 'slug'),
        )

    def __unicode__(self):
        return self.name

    def save(self, **kwargs):
        """
        Sets the Coupon coefficient based on its type
        and forces it to be saved in Umbrella database
        """
        if self.type == self.DISCOUNT:
            self.coefficient = 1
        elif self.type == self.GIFT:
            self.coefficient = 2
        elif self.type == self.PURCHASE_ORDER:
            self.coefficient = 3
        kwargs['using'] = UMBRELLA
        super(Coupon, self).save(**kwargs)

    def to_dict(self):
        var = to_dict(self)
        try:
            del(var['coefficient'])
            del(var['month_quota'])
            del(var['month_winners'])
            del(var['offered_history'])
            del(var['total_offered'])
        except:
            pass
        return var

    def _get_join_reward_pack(self):
        try:
            return JoinRewardPack.objects.using(UMBRELLA).get(coupon=self)
        except JoinRewardPack.DoesNotExist:
            pass
    join_reward_pack = property(_get_join_reward_pack)

    def get_payment_reward_pack(self, floor, ceiling):
        try:
            return PaymentRewardPack.objects.using(UMBRELLA).get(coupon=self, floor=floor, ceiling=ceiling)
        except PaymentRewardPack.DoesNotExist:
            pass


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
    MANUAL = 'Manual'  # Received after a mission

    service = models.ForeignKey(Service, related_name='+')
    member = models.ForeignKey(Member)
    coupon = models.ForeignKey(Coupon, db_index=True)
    count = models.IntegerField(default=0)
    status = models.CharField(max_length=15, default=PREPARED, db_index=True)
    type = models.CharField(max_length=15, default=FREE, db_index=True)
    object_id = models.CharField(max_length=60, blank=True, null=True, db_index=True)
    amount = models.FloatField(blank=True, null=True, db_index=True,
                               help_text="Amount that was paid to trigger the reward.")


class MemberCoupon(Model):
    member = models.ForeignKey(Member)
    coupon = models.ForeignKey(Coupon)

    class Meta:
        abstract = True


class CouponWinner(MemberCoupon):
    """
    Winner of a Coupon
    """
    collected = models.BooleanField(default=False)


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
    service = models.ForeignKey(Service, related_name='+')
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
    Member CR information on a community. This object
    is supposed to exist only in the Service's database
    and not in umbrella.
    """
    JOIN_REWARD = 1
    PAYMENT_REWARD = 2
    MANUAL_REWARD = 3
    FREE_REWARD = 4
    member = models.OneToOneField(Member)
    reward_score = models.IntegerField(default=JOIN_REWARD, db_index=True)
    coupon_score = models.IntegerField(default=0, db_index=True)
    last_reward_date = models.DateTimeField(default=timezone.now, db_index=True)


class CRBillingPlan(Model):
    """
    Billing Plan ikwen charge Operator who
    want to launch a conf campaign
    """
    FREE_TEST = 'free-test'
    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField()
    audience_size = models.IntegerField()
    raw_monthly_cost = models.FloatField()
    sms_monthly_cost = models.FloatField()
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "CR Billing Plans"

    def __unicode__(self):
        return self.name


class CROperatorProfile(AbstractWatchModel):
    service = models.OneToOneField(Service, related_name='+')
    plan = models.ForeignKey(CRBillingPlan, db_index=True)
    billing_cycle = models.CharField(max_length=30, blank=True,
                                     choices=Service.BILLING_CYCLES_CHOICES, default=Service.QUARTERLY)
    monthly_cost = models.FloatField(default=0, db_index=True)
    sms = models.BooleanField(default=False)
    auto_renew = models.BooleanField(default=True)
    expiry = models.DateField(db_index=True)
    is_active = models.BooleanField(default=True)

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

    class Meta:
        verbose_name_plural = "CR Operators"

    def get_status(self):
        count_pending = Coupon.objects.using(UMBRELLA)\
            .filter(service=self.service, status=Coupon.PENDING_FOR_APPROVAL).count()
        if count_pending > 0:
            return Coupon.PENDING_FOR_APPROVAL
        return Coupon.APPROVED

    def _get_details(self):
        return _("Continuous Rewarding: %s plan." % self.plan.name)
    details = property(_get_details)

    def __unicode__(self):
        return str(self.service)


class EarnedReward(Model):
    """
    Reward earned for your action on a Service
    """
    service = models.ForeignKey(Service, related_name='+')
    coupon = models.ForeignKey(Coupon)
    count = models.IntegerField()

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


def purge_coupon(sender, **kwargs):
    """
    Deletes all references to a Coupon whenever its
    status is set to delete=True. We avoid actual deletion
    in order to prevent ForeignKey references issues.
    """
    if sender != Coupon:  # Avoid unending recursive call
        return
    instance = kwargs['instance']
    if not instance.deleted:
        return

    def clear_references(coupon):
        service = coupon.service
        for win in CouponWinner.objects.using(UMBRELLA).filter(coupon=coupon, collected=False):
            win.delete()

        # Avoid memory overflow by processing in chunks of 500
        total = CumulatedCoupon.objects.using(UMBRELLA).filter(coupon=coupon).count()
        chunks = total / 500 + 1
        for i in range(chunks):
            start = i * 500
            finish = (i + 1) * 500
            for cumul in CumulatedCoupon.objects.using(UMBRELLA).filter(coupon=coupon)[start:finish]:
                member = cumul.member
                summary = CouponSummary.objects.using(UMBRELLA).get(service=service, member=member)
                summary.count -= cumul.count
                cumul.delete()
                threshold_reached = False
                for c in CumulatedCoupon.objects.using(UMBRELLA).filter(member=member):
                    if c.count >= c.coupon.heap_size:
                        threshold_reached = True
                        break
                summary.threshold_reached = threshold_reached
                summary.save()
    if getattr(settings, 'UNIT_TESTING', False):
        clear_references(instance)
    else:  # Run in separate thread in production as it may take some time
        Thread(target=clear_references, args=(instance,)).start()


post_save.connect(purge_coupon, dispatch_uid="coupon_post_save_id")