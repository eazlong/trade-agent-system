"""
Django management command to add exchange account with encrypted API keys
"""

import getpass
from django.core.management.base import BaseCommand
from apps.exchange.models import ExchangeAccount


class Command(BaseCommand):
    help = 'Add a new exchange account with encrypted API keys'

    def add_arguments(self, parser):
        parser.add_argument('--exchange', type=str, required=True, help='Exchange name (e.g., binance, okx, bybit)')
        parser.add_argument('--label', type=str, help='Account label/description')

    def handle(self, *args, **options):
        exchange = options['exchange']
        label = options['label'] or input(f'Enter account label (optional, default: {exchange}): ') or exchange

        # Securely get API key and secret without echoing
        api_key = getpass.getpass("Enter API Key: ")
        api_secret = getpass.getpass("Enter API Secret: ")

        # Encrypt the API key and secret before saving
        fernet = ExchangeAccount()._get_fernet()
        api_key_enc = fernet.encrypt(api_key.encode())
        api_secret_enc = fernet.encrypt(api_secret.encode())

        # Create the account with encrypted keys
        account = ExchangeAccount.objects.create(
            exchange=exchange,
            label=label,
            api_key_enc=api_key_enc,
            api_secret_enc=api_secret_enc,
        )

        self.stdout.write(
            self.style.SUCCESS(f'Successfully added exchange account: {account}')
        )