#!/usr/bin/env python
# -*- coding: utf-8 -*-
import sys
import json
import configparser
import argparse
import subprocess
from azure.identity import ClientSecretCredential, DefaultAzureCredential
from azure.mgmt.resource import ResourceManagementClient, SubscriptionClient
from azure.core.exceptions import HttpResponseError
from libs.platforms.platform import Platform
from libs.platforms.platform import PlatformArguments


class Aro(Platform):
    def __init__(self, arguments, logging, utils, es):
        super().__init__(arguments, logging, utils, es)

        self.environment["azure_region"] = arguments["azure_region"]
        self.environment["azure_credentials_file"] = arguments["azure_credentials_file"]
        self.environment['azure_mc_cluster_subscription'] = arguments['azure_mc_subscription']
        self.environment["aro_env"] = arguments["aro_env"]
        self.environment["commands"].append("az")

    def initialize(self):
        super().initialize()

        # Verify Azure credentials file and extract credentials
        self.logging.info(f"Verifying Azure Credentials File {self.environment['azure_credentials_file']}...")
        try:
            with open(self.environment["azure_credentials_file"], 'r') as azure_credentials_file:
                azure_creds = json.load(azure_credentials_file)
        except json.JSONDecodeError:
            # Fallback to line-by-line parsing if not JSON
            self.logging.warning("Credentials file is not in JSON format, attempting line-by-line parsing")
            azure_creds = {}
            with open(self.environment["azure_credentials_file"], 'r') as azure_credentials_file:
                for line in azure_credentials_file:
                    if ':' in line:
                        key, value = line.split(':', 1)
                        azure_creds[key.strip().strip('"\',')] = value.strip().strip('"\',')
        except Exception as err:
            self.logging.error(f"Failed to read Azure credentials file {self.environment['azure_credentials_file']}")
            self.logging.error(err)
            sys.exit("Exiting...")

        # Verify required keys
        required_keys = ["tenantId", "subscriptionId", "ClientId", "ClientSecret"]
        for key in required_keys:
            if key not in azure_creds:
                self.logging.error(f"Missing {key} on Azure credentials file {self.environment['azure_credentials_file']}")
                sys.exit("Exiting...")

        self.environment['subscription_id'] = azure_creds["subscriptionId"]
        self.environment['tenant_id'] = azure_creds["tenantId"]
        self.logging.info(f"Azure Credentials File {self.environment['azure_credentials_file']} verified")

        # Create credential object using Azure SDK
        self.logging.info("Initializing Azure SDK credentials")
        try:
            self.credential = ClientSecretCredential(
                tenant_id=azure_creds["tenantId"],
                client_id=azure_creds["ClientId"],
                client_secret=azure_creds["ClientSecret"]
            )
        except Exception as err:
            self.logging.warning(f"Failed to create ClientSecretCredential, trying DefaultAzureCredential: {err}")
            self.credential = DefaultAzureCredential()

        # Initialize Azure Resource Management Client
        self.resource_client = ResourceManagementClient(self.credential, self.environment['subscription_id'])

        # Verify subscription access using SubscriptionClient
        self.logging.info("Verifying Azure subscription access")
        try:
            subscription_client = SubscriptionClient(self.credential)
            subscription = subscription_client.subscriptions.get(self.environment['subscription_id'])
            self.logging.info(f"Successfully authenticated to subscription: {subscription.display_name} ({subscription.subscription_id})")
        except HttpResponseError as err:
            self.logging.error(f"Failed to access subscription {self.environment['subscription_id']}: {err}")
            sys.exit("Exiting...")
        except Exception as err:
            self.logging.error(f"Unexpected error verifying subscription: {err}")
            sys.exit("Exiting...")

        self.logging.info("Azure SDK authentication successful")

        # Login to Azure CLI using service principal (required for az ad app create)
        self.logging.info("Logging in to Azure CLI using service principal")
        try:
            az_login_cmd = [
                "az", "login", "--service-principal",
                "--username", azure_creds["ClientId"],
                "--password", azure_creds["ClientSecret"],
                "--tenant", azure_creds["tenantId"]
            ]
            az_login_result = subprocess.run(az_login_cmd, capture_output=True, text=True, timeout=60)

            if az_login_result.returncode != 0:
                self.logging.warning(f"Azure CLI login failed: {az_login_result.stderr}")
                self.logging.warning("Some operations requiring Azure CLI (e.g., az ad app create) may fail")
            else:
                self.logging.info("Azure CLI login successful")

                # Set the subscription
                az_account_set_cmd = [
                    "az", "account", "set",
                    "--subscription", azure_creds["subscriptionId"]
                ]
                az_account_set_result = subprocess.run(az_account_set_cmd, capture_output=True, text=True, timeout=30)

                if az_account_set_result.returncode != 0:
                    self.logging.warning(f"Failed to set Azure CLI subscription: {az_account_set_result.stderr}")
                else:
                    self.logging.info(f"Azure CLI subscription set to {azure_creds['subscriptionId']}")
        except subprocess.TimeoutExpired:
            self.logging.warning("Azure CLI login timed out")
        except Exception as err:
            self.logging.warning(f"Unexpected error during Azure CLI login: {err}")
            self.logging.warning("Some operations requiring Azure CLI (e.g., az ad app create) may fail")

    def platform_cleanup(self):
        super().platform_cleanup()

    def create_cluster(self, platform, cluster_name):
        super().create_cluster(platform, cluster_name)

    def delete_cluster(self, platform, cluster_name):
        super().delete_cluster(platform, cluster_name)

    def get_workers_ready(self, kubeconfig, cluster_name):
        super().get_workers_ready(kubeconfig, cluster_name)
        return Platform.get_workers_ready(self, kubeconfig, cluster_name)

    def get_metadata(self, platform, cluster_name):
        super().get_metadata(platform, cluster_name)
        metadata = {}
        # TODO Implement metadata logic when ready to use
        return metadata

    def watcher(self):
        super().watcher()


class AroArguments(PlatformArguments):
    def __init__(self, parser, config_file, environment):
        super().__init__(parser, config_file, environment)
        EnvDefault = self.EnvDefault

        parser.add_argument("--aro-env", action=EnvDefault, env=environment, default='production', envvar="HCP_BURNER_ARO_ENV", help="ARO Environment")
        parser.add_argument("--azure-credentials-file", action=EnvDefault, env=environment, envvar="HCP_BURNER_AZURE_CREDENTIALS_FILE", help="Azure credentials file")
        parser.add_argument("--azure-region", action=EnvDefault, env=environment, envvar="HCP_BURNER_AZURE_REGION", default='eastus', help="Azure Region")
        parser.add_argument("--azure-mc-subscription", action=EnvDefault, env=environment, envvar="HCP_BURNER_MC_SUBSCRIPTION", help="Azure Subscription where MC Cluster is installed")

        if config_file:
            config = configparser.ConfigParser()
            config.read(config_file)
            defaults = {}
            defaults.update(dict(config.items("Platform:Aro")))
            parser.set_defaults(**defaults)

        temp_args, temp_unknown_args = parser.parse_known_args()
        if not temp_args.azure_credentials_file:
            parser.error("hcp-burner.py: error: the following arguments (or equivalent definition) are required: --azure-credentials-file")

    class EnvDefault(argparse.Action):
        def __init__(self, env, envvar, default=None, **kwargs):
            default = env[envvar] if envvar in env else default
            super(AroArguments.EnvDefault, self).__init__(
                default=default, **kwargs
            )

        def __call__(self, parser, namespace, values, option_string=None):
            setattr(namespace, self.dest, values)
