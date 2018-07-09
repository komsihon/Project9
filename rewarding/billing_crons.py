#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ikwen.conf.settings")

from django.db.models import Q
from datetime import datetime, timedelta
from django.conf import settings
from django.contrib.auth.models import Group
from django.core import mail
from django.core.mail import EmailMessage
from django.core.urlresolvers import reverse
from django.utils import timezone
from django.utils.module_loading import import_by_path
from django.utils.log import AdminEmailHandler
from django.utils.translation import gettext as _
from ikwen.accesscontrol.models import SUDO

from ikwen.core.models import Config, QueuedSMS, Service
from ikwen.core.utils import get_service_instance, send_sms, add_event
from ikwen.core.utils import get_mail_content
from ikwen.billing.models import Invoice, InvoicingConfig, INVOICES_SENT_EVENT, \
    NEW_INVOICE_EVENT, INVOICE_REMINDER_EVENT, REMINDERS_SENT_EVENT, OVERDUE_NOTICE_EVENT, OVERDUE_NOTICES_SENT_EVENT, \
    SUSPENSION_NOTICES_SENT_EVENT, SERVICE_SUSPENDED_EVENT, SendingReport, IkwenInvoiceItem, InvoiceEntry
from ikwen.billing.utils import get_invoice_generated_message, get_invoice_reminder_message, \
    get_invoice_overdue_message, \
    get_service_suspension_message, get_next_invoice_number, get_subscription_model, get_billing_cycle_months_count, \
    pay_with_wallet_balance
from ikwen.partnership.models import ApplicationRetailConfig

import logging.handlers
logger = logging.getLogger('crons.error')
logger.setLevel(logging.DEBUG)
file_handler = logging.handlers.RotatingFileHandler('billing_crons.log', 'w', 1000000, 4)
file_handler.setLevel(logging.INFO)
f = logging.Formatter('%(levelname)-10s %(asctime)-27s %(message)s')
file_handler.setFormatter(f)
email_handler = AdminEmailHandler()
email_handler.setLevel(logging.ERROR)
email_handler.setFormatter(f)
logger.addHandler(file_handler)
logger.addHandler(email_handler)

Subscription = get_subscription_model()


def send_invoices():
    """
    This cron task simply sends the Invoice *invoicing_gap* days before Subscription *expiry*
    """
    service = get_service_instance()
    config = service.config
    now = datetime.now()
    invoicing_config = InvoicingConfig.objects.all()[0]
    connection = mail.get_connection()
    try:
        connection.open()
    except:
        logger.error(u"Connexion error", exc_info=True)
    count, total_amount = 0, 0
    reminder_date_time = now + timedelta(days=invoicing_config.gap)
    subscription_qs = Subscription.objects.filter(status=Subscription.ACTIVE,
                                                  monthly_cost__gt=0, expiry=reminder_date_time.date())
    logger.debug("%d Service candidate for invoice issuance." % subscription_qs.count())
    for subscription in subscription_qs:
        if getattr(settings, 'IS_IKWEN', False):
            if subscription.version == Service.FREE:
                continue
        member = subscription.member
        number = get_next_invoice_number()
        months_count = 1
        if config.__dict__.get('separate_billing_cycle', True):
            months_count = get_billing_cycle_months_count(subscription.billing_cycle)
            amount = subscription.monthly_cost * months_count
        else:
            amount = subscription.product.cost

        path_before = getattr(settings, 'BILLING_BEFORE_NEW_INVOICE', None)
        if path_before:
            before_new_invoice = import_by_path(path_before)
            val = before_new_invoice(subscription)
            if val is not None:  # Returning a not None value cancels the generation of a new Invoice for this Service
                continue

        entries = []
        if type(subscription) is Service:
            partner = service.retailer
            if partner:
                retail_config = ApplicationRetailConfig.objects.get(partner=partner, app=service.app)
                ikwen_price = retail_config.ikwen_monthly_cost
            else:
                ikwen_price = service.monthly_cost
            hosting = IkwenInvoiceItem(label=_('Website hosting'), price=ikwen_price, amount=service.monthly_cost)
            short_description = _("Project %s" % service.domain)
            entry = InvoiceEntry(item=hosting, short_description=short_description, quantity=months_count, total=amount)
            entries = [entry]
        invoice = Invoice.objects.create(subscription=subscription, amount=amount, number=number,
                                         due_date=subscription.expiry, months_count=months_count, entries=entries)
        count += 1
        total_amount += amount
        add_event(service, NEW_INVOICE_EVENT, member=member, object_id=invoice.id)

        paid_by_wallet_debit = False
        if getattr(settings, 'IS_IKWEN', False) and subscription.balance >= invoice.amount:
            pay_with_wallet_balance(invoice)
            paid_by_wallet_debit = True
            logger.debug("Invoice for %s paid by wallet debit" % subscription.domain)

        subject, message, sms_text = get_invoice_generated_message(invoice)

        if member.email:
            invoice_url = service.url + reverse('billing:invoice_detail', args=(invoice.id,))
            if paid_by_wallet_debit:
                subject = _("Thanks for your payment")
                invoice_url = service.url + reverse('billing:invoice_detail', args=(invoice.id,))
                context = {'wallet_debit': True, 'invoice': invoice, 'config': config,
                           'invoice_url': invoice_url, 'cta': _("View invoice")}
                html_content = get_mail_content(subject, '', template_name='billing/mails/notice.html',
                                                extra_context=context)
            else:
                html_content = get_mail_content(subject, message, template_name='billing/mails/notice.html',
                                                extra_context={'invoice_url': invoice_url, 'cta': _("Pay now")})
            # Sender is simulated as being no-reply@company_name_slug.com to avoid the mail
            # to be delivered to Spams because of origin check.
            sender = '%s <no-reply@%s>' % (config.company_name, service.domain)
            msg = EmailMessage(subject, html_content, sender, [member.email])
            msg.content_subtype = "html"
            invoice.last_reminder = timezone.now()
            try:
                if msg.send():
                    logger.debug("1st Invoice reminder for %s sent to %s" % (subscription.domain, member.email))
                    if not paid_by_wallet_debit:
                        invoice.reminders_sent = 1
                        invoice.save()
                else:
                    logger.error(u"Invoice #%s generated but mail not sent to %s" % (number, member.email),
                                 exc_info=True)
            except:
                logger.error(u"Connexion error on Invoice #%s to %s" % (number, member.email), exc_info=True)

        if sms_text:
            if member.phone:
                if config.sms_sending_method == Config.HTTP_API:
                    send_sms(member.phone, sms_text)
                else:
                    QueuedSMS.objects.create(recipient=member.phone, text=sms_text)

        path_after = getattr(settings, 'BILLING_AFTER_NEW_INVOICE', None)
        if path_after:
            after_new_invoice = import_by_path(path_after)
            after_new_invoice(invoice)

    try:
        connection.close()
    finally:
        if count > 0:
            report = SendingReport.objects.create(count=count, total_amount=total_amount)
            sudo_group = Group.objects.get(name=SUDO)
            add_event(service, INVOICES_SENT_EVENT, group_id=sudo_group.id, object_id=report.id)


def send_invoice_reminders():
    """
    This cron task sends Invoice reminder notice to the client if unpaid
    """
    service = get_service_instance()
    config = service.config
    now = datetime.now()
    invoicing_config = InvoicingConfig.objects.all()[0]
    connection = mail.get_connection()
    try:
        connection.open()
    except:
        logger.error(u"Connexion error", exc_info=True)
    count, total_amount = 0, 0
    invoice_qs = Invoice.objects.filter(status=Invoice.PENDING, due_date__gte=now.date(), last_reminder__isnull=False)
    print "%d invoice(s) candidate for reminder." % invoice_qs.count()
    for invoice in invoice_qs:
        subscription = invoice.subscription
        if getattr(settings, 'IS_IKWEN', False):
            if subscription.version == Service.FREE:
                continue
        diff = now - invoice.last_reminder
        if diff.days == invoicing_config.reminder_delay:
            print "Processing invoice for Service %s" % str(invoice.subscription)
            count += 1
            total_amount += invoice.amount
            member = invoice.subscription.member
            add_event(service, INVOICE_REMINDER_EVENT, member=member, object_id=invoice.id)
            print "Event posted to %s's Console" % member.username
            subject, message, sms_text = get_invoice_reminder_message(invoice)
            if member.email:
                invoice_url = service.url + reverse('billing:invoice_detail', args=(invoice.id,))
                html_content = get_mail_content(subject, message, template_name='billing/mails/notice.html',
                                                extra_context={'invoice_url': invoice_url, 'cta': _("Pay now")})
                # Sender is simulated as being no-reply@company_name_slug.com to avoid the mail
                # to be delivered to Spams because of origin check.
                sender = '%s <no-reply@%s>' % (config.company_name, service.domain)
                msg = EmailMessage(subject, html_content, sender, [member.email])
                msg.content_subtype = "html"
                invoice.last_reminder = timezone.now()
                print "Sending mail to %s" % member.email
                try:
                    if msg.send():
                        print "Mail sent to %s" % member.email
                        invoice.reminders_sent += 1
                    else:
                        print "Sending mail to %s failed" % member.email
                        logger.error(u"Reminder mail for Invoice #%s not sent to %s" % (invoice.number, member.email), exc_info=True)
                except:
                    print "Sending mail to %s failed" % member.email
                    logger.error(u"Connexion error on Invoice #%s to %s" % (invoice.number, member.email), exc_info=True)
                invoice.save()
            if sms_text:
                if member.phone:
                    if config.sms_sending_method == Config.HTTP_API:
                        send_sms(member.phone, sms_text)
                    else:
                        QueuedSMS.objects.create(recipient=member.phone, text=sms_text)
    try:
        connection.close()
    finally:
        if count > 0:
            report = SendingReport.objects.create(count=count, total_amount=total_amount)
            sudo_group = Group.objects.get(name=SUDO)
            add_event(service, REMINDERS_SENT_EVENT, group_id=sudo_group.id, object_id=report.id)


def send_invoice_overdue_notices():
    """
    This cron task sends notice of Invoice overdue
    """
    service = get_service_instance()
    config = service.config
    now = timezone.now()
    invoicing_config = InvoicingConfig.objects.all()[0]
    connection = mail.get_connection()
    try:
        connection.open()
    except:
        logger.error(u"Connexion error", exc_info=True)
    count, total_amount = 0, 0
    invoice_qs = Invoice.objects.filter(Q(status=Invoice.PENDING) | Q(status=Invoice.OVERDUE),
                                        due_date__lt=now, overdue_notices_sent__lt=3)
    print "%d invoice(s) candidate for overdue notice." % invoice_qs.count()
    for invoice in invoice_qs:
        subscription = invoice.subscription
        if getattr(settings, 'IS_IKWEN', False):
            if subscription.version == Service.FREE:
                continue
        if invoice.last_overdue_notice:
            diff = now - invoice.last_overdue_notice
        else:
            invoice.status = Invoice.OVERDUE
            invoice.save()
        if not invoice.last_overdue_notice or diff.days == invoicing_config.overdue_delay:
            print "Processing invoice for Service %s" % str(invoice.subscription)
            count += 1
            total_amount += invoice.amount
            member = invoice.subscription.member
            add_event(service, OVERDUE_NOTICE_EVENT, member=member, object_id=invoice.id)
            print "Event posted to %s's Console" % member.username
            subject, message, sms_text = get_invoice_overdue_message(invoice)
            if member.email:
                invoice_url = service.url + reverse('billing:invoice_detail', args=(invoice.id,))
                html_content = get_mail_content(subject, message, template_name='billing/mails/notice.html',
                                                extra_context={'invoice_url': invoice_url, 'cta': _("Pay now")})
                # Sender is simulated as being no-reply@company_name_slug.com to avoid the mail
                # to be delivered to Spams because of origin check.
                sender = '%s <no-reply@%s>' % (config.company_name, service.domain)
                msg = EmailMessage(subject, html_content, sender, [member.email])
                msg.content_subtype = "html"
                invoice.last_overdue_notice = timezone.now()
                print "Sending mail to %s" % member.email
                try:
                    if msg.send():
                        print "Mail sent to %s" % member.email
                        invoice.overdue_notices_sent += 1
                    else:
                        print "Sending mail to %s failed" % member.email
                        logger.error(u"Overdue notice for Invoice #%s not sent to %s" % (invoice.number, member.email), exc_info=True)
                except:
                    print "Sending mail to %s failed" % member.email
                    logger.error(u"Connexion error on Invoice #%s to %s" % (invoice.number, member.email), exc_info=True)
                invoice.save()
            if sms_text:
                if member.phone:
                    if config.sms_sending_method == Config.HTTP_API:
                        send_sms(member.phone, sms_text)
                    else:
                        QueuedSMS.objects.create(recipient=member.phone, text=sms_text)
    try:
        connection.close()
    finally:
        if count > 0:
            report = SendingReport.objects.create(count=count, total_amount=total_amount)
            sudo_group = Group.objects.get(name=SUDO)
            add_event(service, OVERDUE_NOTICES_SENT_EVENT, group_id=sudo_group.id, object_id=report.id)


def suspend_customers_services():
    """
    This cron task shuts down service and sends notice of Service suspension
    for Invoices which tolerance is exceeded.
    """
    service = get_service_instance()
    config = service.config
    now = timezone.now()
    invoicing_config = InvoicingConfig.objects.all()[0]
    connection = mail.get_connection()
    try:
        connection.open()
    except:
        logger.error(u"Connexion error", exc_info=True)
    count, total_amount = 0, 0
    deadline = now - timedelta(days=invoicing_config.tolerance)
    invoice_qs = Invoice.objects.filter(due_date__lte=deadline, status=Invoice.OVERDUE)
    print "%d invoice(s) candidate for service suspension." % invoice_qs.count()
    for invoice in invoice_qs:
        subscription = invoice.subscription
        if getattr(settings, 'IS_IKWEN', False):
            if subscription.version == Service.FREE:
                continue
        invoice.status = Invoice.EXCEEDED
        invoice.save()
        action = getattr(settings, 'SERVICE_SUSPENSION_ACTION', None)
        if action:
            count += 1
            total_amount += invoice.amount
            action = import_by_path(action)
            try:
                action(subscription)
            except:
                logger.error("Error while processing subscription %s" % str(subscription), exc_info=True)
                continue
            member = subscription.member
            add_event(service, SERVICE_SUSPENDED_EVENT, member=member, object_id=invoice.id)
            print "Event posted to %s's Console" % member.username
            subject, message, sms_text = get_service_suspension_message(invoice)
            if member.email:
                invoice_url = service.url + reverse('billing:invoice_detail', args=(invoice.id,))
                html_content = get_mail_content(subject, message, template_name='billing/mails/notice.html',
                                                extra_context={'invoice_url': invoice_url, 'cta': _("Pay now")})
                # Sender is simulated as being no-reply@company_name_slug.com to avoid the mail
                # to be delivered to Spams because of origin check.
                sender = '%s <no-reply@%s>' % (config.company_name, service.domain)
                msg = EmailMessage(subject, html_content, sender, [member.email])
                msg.content_subtype = "html"
                print "Sending mail to %s" % member.email
                try:
                    if msg.send():
                        print "Mail sent to %s" % member.email
                    else:
                        print "Sending mail to %s failed" % member.email
                        logger.error(u"Notice of suspension for Invoice #%s not sent to %s" % (invoice.number, member.email), exc_info=True)
                except:
                    print "Sending mail to %s failed" % member.email
                    logger.error(u"Connexion error on Invoice #%s to %s" % (invoice.number, member.email), exc_info=True)
            if sms_text:
                if member.phone:
                    if config.sms_sending_method == Config.HTTP_API:
                        send_sms(member.phone, sms_text)
                    else:
                        QueuedSMS.objects.create(recipient=member.phone, text=sms_text)
    try:
        connection.close()
    finally:
        if count > 0:
            report = SendingReport.objects.create(count=count, total_amount=total_amount)
            sudo_group = Group.objects.get(name=SUDO)
            add_event(service, SUSPENSION_NOTICES_SENT_EVENT, group_id=sudo_group.id, object_id=report.id)


if __name__ == "__main__":
    try:
        send_invoices()
        send_invoice_reminders()
        send_invoice_overdue_notices()
        suspend_customers_services()
    except:
        logger.error(u"Fatal error occured", exc_info=True)
