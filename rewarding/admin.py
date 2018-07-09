from django.contrib import admin


class CouponAdmin(admin.ModelAdmin):
    fields = ('name', 'type', 'description', 'month_quota', )
