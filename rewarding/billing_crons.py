#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "conf.settings")

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
from ikwen.rewarding.models import CROperatorProfile

import logging.handlers
logger = logging.getLogger('ikwen.rewarding')
f = logging.Formatter('%(levelname)-10s %(asctime)-27s %(message)s')
email_handler = AdminEmailHandler()
email_handler.setLevel(logging.ERROR)
email_handler.setFormatter(f)
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
    subscription_qs = Subscription.objects.filter(monthly_cost__gt=0, expiry=reminder_date_time.date())
    logger.debug("%d CR Operators candidate for invoice issuance." % subscription_qs.count())
    for subscription in subscription_qs:
        if subscription.plan.raw_monthly_cost == 0:
            continue
        cr_service = subscription.service
        member = cr_service.member
        number = get_next_invoice_number()
        months_count = get_billing_cycle_months_count(subscription.billing_cycle)
        amount = subscription.monthly_cost * months_count

        ikwen_price = subscription.monthly_cost
        hosting = IkwenInvoiceItem(label=_('Website hosting'), price=ikwen_price, amount=subscription.monthly_cost)
        short_description = _("Continuous Rewarding Program for %s" % cr_service.domain)
        entry = InvoiceEntry(item=hosting, short_description=short_description, quantity=months_count, total=amount)
        entries = [entry]
        invoice = Invoice.objects.create(subscription=subscription, amount=amount, number=number,
                                         due_date=subscription.expiry, months_count=months_count, entries=entries)
        count += 1
        total_amount += amount
        add_event(service, NEW_INVOICE_EVENT, member=member, object_id=invoice.id)

        paid_by_wallet_debit = False
        if cr_service.balance >= invoice.amount:
            pay_with_wallet_balance(invoice)
            paid_by_wallet_debit = True
            logger.debug("CR Invoice for %s paid by wallet debit" % cr_service.domain)

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
                    logger.debug("1st Invoice reminder for %s sent to %s" % (cr_service.domain, member.email))
                    if not paid_by_wallet_debit:
                        invoice.reminders_sent = 1
                        invoice.save()
                else:
                    logger.error(u"Invoice #%s generated but mail not sent to %s" % (number, member.email),
                                 exc_info=True)
            except:
                logger.error(u"Connexion error on Invoice #%s to %s" % (number, member.email), exc_info=True)

        path_after = getattr(settings, 'BILLING_AFTER_NEW_INVOICE', None)
        if path_after:
            after_new_invoice = import_by_path(path_after)
            after_new_invoice(invoice)

    try:
        connection.close()
    finally:
        pass


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
    logger.debug("%d invoice(s) candidate for reminder." % invoice_qs.count())
    for invoice in invoice_qs:
        subscription = invoice.subscription
        if subscription.plan.raw_monthly_cost == 0:
            continue
        diff = now - invoice.last_reminder
        if diff.days == invoicing_config.reminder_delay:
            count += 1
            total_amount += invoice.amount
            member = subscription.service.member
            add_event(service, INVOICE_REMINDER_EVENT, member=member, object_id=invoice.id)
            logger.debug("Event posted to %s's Console" % member.username)
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
                logger.debug("Sending mail to %s" % member.email)
                try:
                    if msg.send():
                        logger.debug("Mail sent to %s" % member.email)
                        invoice.reminders_sent += 1
                    else:
                        logger.error(u"Reminder mail for Invoice #%s not sent to %s" % (invoice.number, member.email), exc_info=True)
                except:
                    logger.error(u"Connexion error on Invoice #%s to %s" % (invoice.number, member.email), exc_info=True)
                invoice.save()
    try:
        connection.close()
    finally:
        pass


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
    logger.debug("%d invoice(s) candidate for overdue notice." % invoice_qs.count())
    for invoice in invoice_qs:
        subscription = invoice.subscription
        if subscription.plan.raw_monthly_cost == 0:
            continue
        if invoice.last_overdue_notice:
            diff = now - invoice.last_overdue_notice
        else:
            invoice.status = Invoice.OVERDUE
            invoice.save()
        if not invoice.last_overdue_notice or diff.days == invoicing_config.overdue_delay:
            count += 1
            total_amount += invoice.amount
            member = subscription.service.member
            add_event(service, OVERDUE_NOTICE_EVENT, member=member, object_id=invoice.id)
            logger.debug("Event posted to %s's Console" % member.username)
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
                logger.debug("Sending mail to %s" % member.email)
                try:
                    if msg.send():
                        logger.debug("Mail sent to %s" % member.email)
                        invoice.overdue_notices_sent += 1
                    else:
                        logger.error(u"Overdue notice for Invoice #%s not sent to %s" % (invoice.number, member.email), exc_info=True)
                except:
                    logger.error(u"Connexion error on Invoice #%s to %s" % (invoice.number, member.email), exc_info=True)
                invoice.save()
    try:
        connection.close()
    finally:
        pass


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
    logger.debug("%d invoice(s) candidate for service suspension." % invoice_qs.count())
    for invoice in invoice_qs:
        subscription = invoice.subscription
        if subscription.plan.raw_monthly_cost == 0:
            continue
        invoice.status = Invoice.EXCEEDED
        invoice.save()
        count += 1
        total_amount += invoice.amount
        try:
            subscription.is_active = False
            subscription.save()
        except:
            logger.error("Error while processing subscription %s" % str(subscription), exc_info=True)
            continue
        member = subscription.service.member
        add_event(service, SERVICE_SUSPENDED_EVENT, member=member, object_id=invoice.id)
        logger.debug("Event posted to %s's Console" % member.username)
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
            logger.debug("Sending mail to %s" % member.email)
            try:
                if msg.send():
                    logger.debug("Mail sent to %s" % member.email)
                else:
                    logger.error(u"Notice of suspension for Invoice #%s not sent to %s" % (invoice.number, member.email), exc_info=True)
            except:
                logger.error(u"Connexion error on Invoice #%s to %s" % (invoice.number, member.email), exc_info=True)
    try:
        connection.close()
    finally:
        pass


if __name__ == "__main__":
    try:
        send_invoices()
        send_invoice_reminders()
        send_invoice_overdue_notices()
        suspend_customers_services()
    except:
        logger.error(u"Fatal error occured", exc_info=True)
