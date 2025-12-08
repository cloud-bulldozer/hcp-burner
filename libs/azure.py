#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Module to set Azure related variables and validate inputs
"""

import json
import os
import sys


class Azure:
    """Azure Class"""

    def __init__(self, logging, credentials_file):
        self.logging = logging
        self.credentials_file = credentials_file
        if os.path.exists(credentials_file):
            self.logging.info(f"Azure credentials file found: {credentials_file}. Loading account information")
            self.azure_credentials = {}
            try:
                # Try to read as JSON first
                with open(credentials_file, 'r') as f:
                    self.azure_credentials = json.load(f)
            except json.JSONDecodeError:
                # If not JSON, try to parse as key-value pairs (line by line)
                self.logging.warning("Credentials file is not in JSON format, attempting line-by-line parsing")
                self.azure_credentials = {}
                with open(credentials_file, 'r') as f:
                    for line in f:
                        if ':' in line:
                            key, value = line.split(':', 1)
                            self.azure_credentials[key.strip().strip('"\',')] = value.strip().strip('"\',')

            # Validate required keys
            required_keys = ["tenantId", "subscriptionId", "ClientId", "ClientSecret"]
            missing_keys = [key for key in required_keys if key not in self.azure_credentials]

            if missing_keys:
                self.logging.error(f"Missing required credentials in file {credentials_file}: {', '.join(missing_keys)}")
                sys.exit("Exiting...")

            self.logging.info(f"Azure configuration verified for file {credentials_file}")
            self.logging.debug(f"Azure Credentials: tenantId={self.azure_credentials.get('tenantId', 'N/A')}, subscriptionId={self.azure_credentials.get('subscriptionId', 'N/A')}, ClientId={self.azure_credentials.get('ClientId', 'N/A')}")
        else:
            self.credentials_file = credentials_file
            self.logging.info("Azure credentials file is not provided, so Azure environment variables are being used")
            self.azure_credentials = {}

    def set_azure_envvars(self, azure_region):
        """Get Azure information from the credentials_file if provided and set related environment vars"""
        if self.credentials_file != "" and os.path.exists(self.credentials_file):
            os.environ["AZURE_TENANT_ID"] = self.azure_credentials["tenantId"]
            os.environ["AZURE_SUBSCRIPTION_ID"] = self.azure_credentials["subscriptionId"]
            os.environ["AZURE_CLIENT_ID"] = self.azure_credentials["ClientId"]
            os.environ["AZURE_CLIENT_SECRET"] = self.azure_credentials["ClientSecret"]
            os.environ["AZURE_REGION"] = azure_region
            os.environ["AZURE_CREDENTIALS_FILE"] = self.credentials_file

    def set_azure_environment(self, azure_region):
        """Get Azure information from the credentials_file if provided and save it on the environment object"""
        azure = {}
        if self.credentials_file == "" or not os.path.exists(self.credentials_file):
            self.logging.info("Azure credentials file is not provided, so Azure environment variables are being used")
            azure['tenant_id'] = os.environ.get("AZURE_TENANT_ID", "")
            azure['subscription_id'] = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
            azure['client_id'] = os.environ.get("AZURE_CLIENT_ID", "")
            azure['client_secret'] = os.environ.get("AZURE_CLIENT_SECRET", "")
        else:
            azure['tenant_id'] = self.azure_credentials["tenantId"]
            azure['subscription_id'] = self.azure_credentials["subscriptionId"]
            azure['client_id'] = self.azure_credentials["ClientId"]
            azure['client_secret'] = self.azure_credentials["ClientSecret"]
        azure['region'] = azure_region
        azure['credentials_file'] = self.credentials_file
        return azure

    def get_credentials(self):
        """Get Azure credentials dictionary"""
        return self.azure_credentials
