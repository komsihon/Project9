
from django.conf.urls import patterns, url
from django.contrib.auth.decorators import login_required, permission_required

from ikwen.rewarding.views import Configuration, ChangeCoupon, Dashboard, CouponDetail

urlpatterns = patterns(
    '',
    url(r'^dashboard/$', permission_required('rewarding.ik_manage_rewarding')(Dashboard.as_view()), name='dashboard'),
    url(r'^configuration/$', permission_required('rewarding.ik_manage_rewarding')(Configuration.as_view()), name='configuration'),
    url(r'^changeCoupon/$', permission_required('rewarding.ik_manage_rewarding')(ChangeCoupon.as_view()), name='change_coupon'),
    url(r'^changeCoupon/(?P<object_id>[-\w]+)/$', permission_required('rewarding.ik_manage_rewarding')(ChangeCoupon.as_view()), name='change_coupon'),
    url(r'^coupon/(?P<slug>[-\w]+)$', CouponDetail.as_view(), name='coupon_detail'),
)
