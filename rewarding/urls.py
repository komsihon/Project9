
from django.urls import path
from django.contrib.auth.decorators import permission_required

from ikwen.rewarding.views import Configuration, ChangeCoupon, Dashboard, CouponDetail, upload_coupon_image

urlpatterns = [
    path('dashboard/', permission_required('rewarding.ik_manage_rewarding')(Dashboard.as_view()), name='dashboard'),
    path('configuration/', permission_required('rewarding.ik_manage_rewarding')(Configuration.as_view()), name='configuration'),
    path('changeCoupon/', permission_required('rewarding.ik_manage_rewarding')(ChangeCoupon.as_view()), name='change_coupon'),
    path('changeCoupon/<object_id>)/', permission_required('rewarding.ik_manage_rewarding')(ChangeCoupon.as_view()), name='change_coupon'),
    path('upload_coupon_image', upload_coupon_image, name='upload_coupon_image'),
    path('coupon_detail', CouponDetail.as_view(), name='coupon_detail'),
]
